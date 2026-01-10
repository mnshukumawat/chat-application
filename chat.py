from flask import Flask, render_template, request, redirect, session, flash, jsonify
from flask_socketio import SocketIO, emit, join_room
import mysql.connector

app = Flask(__name__)
app.secret_key = "secret123"
socketio = SocketIO(app, async_mode='threading')

# ---------------- DB CONNECTION ----------------
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="your password",
        database="chat_app",
        autocommit=True
    )

# ---------------- ONLINE USERS & SOCKETS ----------------
online_users = set()
user_sockets = {}  # username -> set of socket ids

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
        user_sockets.pop(session['user'], None)
        session.pop('user')
    return redirect('/login')

@app.route('/chat')
def chat():
    if 'user' not in session:
        return redirect('/login')
    return render_template('index.html', user=session['user'])

@app.route('/users')
def users():
    if 'user' not in session:
        return jsonify({"error": "Login required"}), 401

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
    if 'user' not in session:
        return jsonify([])

    sender = session['user']

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
        return jsonify([])

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
    if 'user' not in session:
        return
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
    if 'user' not in session:
        return
    online_users.discard(session['user'])
    if session['user'] in user_sockets:
        user_sockets[session['user']].discard(request.sid)
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
    if 'user' not in session:
        return
    join_room(request.sid)
    if session['user'] not in user_sockets:
        user_sockets[session['user']] = set()
    user_sockets[session['user']].add(request.sid)

@socketio.on('private_message')
def private_message(data):
    if 'user' not in session:
        return

    sender = data['from']
    receiver = data['to']
    msg = data['message']

    db = get_db()
    cursor = db.cursor(buffered=True, dictionary=True)

    cursor.execute("""
    SELECT 1 FROM requests
    WHERE
    (
        (sender=%s AND receiver=%s)
     OR (sender=%s AND receiver=%s)
    )
    AND status='accepted'
    """, (sender, receiver, receiver, sender))

    if not cursor.fetchone():
        cursor.close()
        db.close()
        emit('chat_blocked', room=request.sid)
        return


    cursor.execute("""
        INSERT INTO messages(sender, receiver, message, status)
        VALUES(%s, %s, %s, 'sent')
    """, (sender, receiver, msg))
    msg_id = cursor.lastrowid
    status = 'sent'

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

    for sid in user_sockets.get(sender, []):
        emit('receive_message', payload, room=sid)
    for sid in user_sockets.get(receiver, []):
        emit('receive_message', payload, room=sid)

@socketio.on('mark_seen')
def mark_seen(data):
    if 'user' not in session:
        return

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

    for sid in user_sockets.get(data['sender'], []):
        emit('status_updated', {
            'sender': data['sender'],
            'receiver': data['receiver']
        }, room=sid)

@socketio.on('delete_message')
def delete_message(data):
    if 'user' not in session:
        return

    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("DELETE FROM messages WHERE id=%s", (data['id'],))
    db.commit()
    cursor.close()
    db.close()

    for sid in user_sockets.get(data['sender'], []):
        emit('message_deleted', {'id': data['id']}, room=sid)
    for sid in user_sockets.get(data['receiver'], []):
        emit('message_deleted', {'id': data['id']}, room=sid)

# ---------------- REQUEST SYSTEM ----------------
@socketio.on('send_request')
def send_request(data):
    if 'user' not in session:
        return

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

    for sid in user_sockets.get(data['to'], []):
        emit('receive_request', {'from': data['from']}, room=sid)

@socketio.on('request_response')
def request_response(data):
    if 'user' not in session:
        return

    db = get_db()
    cursor = db.cursor(buffered=True)
    cursor.execute("""
        UPDATE requests
        SET status=%s
        WHERE sender=%s AND receiver=%s
    """, (
        data['response'],
        data['to'],   # original sender
        data['from']  # original receiver
    ))
    db.commit()
    cursor.close()
    db.close()

    for sid in user_sockets.get(data['to'], []):
        emit('request_response', data, room=sid)
        # send refresh event to sender to enable chat immediately
        if data['response'] == 'accepted':
            emit('refresh_allowed_chat', {'with_user': data['from']}, room=sid)

# ---------------- RUN ----------------
if __name__ == '__main__':
    socketio.run(app, debug=False)
