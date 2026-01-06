let socket;
let name;

function startChat(){
  name = document.getElementById("username").value;
  if(name === "") return;

  document.getElementById("login").style.display = "none";
  document.getElementById("chat").style.display = "block";

  socket = io();

  socket.on("message", function(data){
    let div = document.createElement("div");
    div.className = "msg " + (data.name === name ? "me" : "other");
    div.innerText = data.name + ": " + data.msg;
    document.getElementById("messages").appendChild(div);
  });
}

function sendMessage(){
  let msg = document.getElementById("message").value;
  socket.send({name:name, msg:msg});
  document.getElementById("message").value = "";
}
