from flask import Flask, render_template, request, redirect, session, flash, jsonify
from flask_socketio import SocketIO, emit, join_room
import mysql.connector

app = Flask(__name__)
app.secret_key = "secret123"
socketio = SocketIO(app, async_mode='threading')

# ---------------- DB CONNECTION HELPER ----------------
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="maheshkumawat@9116",
        database="chat_app",
        autocommit=True
    )

# 🔥 ONLINE USERS TRACK
online_users = set()

# ---------------- ROUTES ----------------

@app.route('/')
def home():
    return redirect('/login')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        try:
            db = get_db()
            cursor = db.cursor(buffered=True)
            cursor.execute(
                "INSERT INTO users(username,password) VALUES(%s,%s)",
                (request.form['username'], request.form['password'])
            )
            db.commit()
            cursor.close()
            db.close()
            return redirect('/login')
        except mysql.connector.IntegrityError:
            flash("Username exists")
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        db = get_db()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "SELECT * FROM users WHERE username=%s AND password=%s",
            (request.form['username'], request.form['password'])
        )
        if cursor.fetchone():
            session['user'] = request.form['username']
            cursor.execute(
                "UPDATE users SET is_online=1 WHERE username=%s",
                (session['user'],)
            )
            db.commit()
            cursor.close()
            db.close()
            return redirect('/chat')
        cursor.close()
        db.close()
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user' in session:
        db = get_db()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE users SET is_online=0 WHERE username=%s",
            (session['user'],)
        )
        db.commit()
        cursor.close()
        db.close()
        online_users.discard(session['user'])
        session.pop('user')
    return redirect('/login')

@app.route('/chat')
def chat():
    if 'user' not in session:
        return redirect('/login')
    return render_template('index.html', user=session['user'])

@app.route('/users')
def users():
    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT username, is_online
        FROM users
        WHERE username != %s
    """, (session['user'],))
    data = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(data)

@app.route('/get_messages/<receiver>')
def get_messages(receiver):
    sender = session['user']

    # CHECK IF REQUEST ACCEPTED
    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT status
        FROM requests
        WHERE (sender=%s AND receiver=%s)
           OR (sender=%s AND receiver=%s)
    """, (sender, receiver, receiver, sender))
    req = cursor.fetchone()
    cursor.close()
    db.close()

    if not req or req[0] != "accepted":
        return jsonify([])  # block chat

    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        SELECT id, sender, message, status
        FROM messages
        WHERE (sender=%s AND receiver=%s)
           OR (sender=%s AND receiver=%s)
        ORDER BY id
    """, (sender, receiver, receiver, sender))
    data = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify(data)

# ---------------- SOCKETS ----------------

@socketio.on('connect')
def on_connect(auth=None):
    
    if 'user' in session:
        online_users.add(session['user'])
        db = get_db()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE users SET is_online=1 WHERE username=%s",
            (session['user'],)
        )
        db.commit()
        cursor.close()
        db.close()
        emit('user_status_change', broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    if 'user' in session:
        online_users.discard(session['user'])
        db = get_db()
        cursor = db.cursor(buffered=True)
        cursor.execute(
            "UPDATE users SET is_online=0 WHERE username=%s",
            (session['user'],)
        )
        db.commit()
        cursor.close()
        db.close()
        emit('user_status_change', broadcast=True)

@socketio.on('join')
def join_user(data):
    join_room(data['username'])

@socketio.on('private_message')
def private_message(data):
    sender = data['from']
    receiver = data['to']
    msg = data['message']

    db = get_db()
    cursor = db.cursor(buffered=True, dictionary=True)

    # Check if request accepted
    cursor.execute("""
        SELECT status FROM requests
        WHERE (sender=%s AND receiver=%s)
           OR (sender=%s AND receiver=%s)
    """, (sender, receiver, receiver, sender))
    req = cursor.fetchone()
    if not req or req['status'] != 'accepted':
        cursor.close()
        db.close()
        emit('chat_blocked', room=sender)
        return

    # Insert message
    cursor.execute("""
        INSERT INTO messages(sender, receiver, message, status)
        VALUES(%s, %s, %s, 'sent')
    """, (sender, receiver, msg))
    msg_id = cursor.lastrowid
    status = 'sent'

    # Mark delivered if receiver online
    if receiver in online_users:
        status = 'delivered'
        cursor.execute("UPDATE messages SET status='delivered' WHERE id=%s", (msg_id,))

    db.commit()
    cursor.close()
    db.close()

    payload = {
        'id': msg_id,
        'sender': sender,
        'receiver': receiver,
        'message': msg,
        'status': status
    }

    emit('receive_message', payload, room=sender)
    emit('receive_message', payload, room=receiver)

@socketio.on('mark_seen')
def mark_seen(data):
    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE messages
        SET status='seen'
        WHERE sender=%s AND receiver=%s AND status!='seen'
    """, (data['sender'], data['receiver']))
    db.commit()
    cursor.close()
    db.close()
    emit('status_updated', {
        'sender': data['sender'],
        'receiver': data['receiver']
    }, room=data['sender'])

@socketio.on('delete_message')
def delete_message(data):
    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM messages WHERE id=%s", (data['id'],))
    db.commit()
    cursor.close()
    db.close()
    emit('message_deleted', room=data['sender'])
    emit('message_deleted', room=data['receiver'])

# ---------------- REQUEST SYSTEM ----------------

@socketio.on('send_request')
def send_request(data):
    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        INSERT INTO requests(sender, receiver, status)
        VALUES(%s, %s, 'pending')
        ON DUPLICATE KEY UPDATE status='pending'
    """, (data['from'], data['to']))
    db.commit()
    cursor.close()
    db.close()
    emit('receive_request', {'from': data['from']}, room=data['to'])

@socketio.on('request_response')
def request_response(data):
    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE requests
        SET status=%s
        WHERE sender=%s AND receiver=%s
    """, (
        data['response'],
        data['to'],     # original sender
        data['from']    # original receiver
    ))
    db.commit()
    cursor.close()
    db.close()
    emit('request_response', data, room=data['to'])

# ---------------- RUN ----------------
if __name__ == '__main__':
    socketio.run(app, debug=False)
