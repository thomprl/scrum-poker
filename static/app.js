const VOTES = ["0","1/2","1","2","3","5","8","13","20","40","100","?","☕"];
const qs = new URLSearchParams(location.search);
const nameEl = document.getElementById('name');
const roomEl = document.getElementById('room');
const hostHint = document.getElementById('hostHint');
const joinBtn = document.getElementById('joinBtn');
const createBtn = document.getElementById('createBtn');

const roomBar = document.getElementById('roomBar');
const roomCode = document.getElementById('roomCode');
const hostName = document.getElementById('hostName');
const statusText = document.getElementById('statusText');
const revealBtn = document.getElementById('revealBtn');
const resetBtn = document.getElementById('resetBtn');
const copyBtn = document.getElementById('copyBtn');

const voteCard = document.getElementById('voteCard');
const voteGrid = document.getElementById('voteGrid');
const yourVote = document.getElementById('yourVote');

const playersCard = document.getElementById('playersCard');
const playersTbody = document.querySelector('#playersTbl tbody');

const tallyCard = document.getElementById('tallyCard');
const tallyTbody = document.querySelector('#tallyTbl tbody');

let currentRoom = null;
let currentName = null;
let isHost = false;
let SSE = null;

function jfetch(path, body) {
  return fetch(path, { method: "POST", headers: { "Content-Type":"application/json" }, body: JSON.stringify(body || {}) })
    .then(r => r.json());
}

function renderVotes(selected) {
  voteGrid.innerHTML = "";
  VOTES.forEach(v => {
    const btn = document.createElement('button');
    btn.className = "vote-btn" + (selected===v ? " active": "");
    btn.textContent = v;
    btn.onclick = () => {
      jfetch("/api/vote", { room: currentRoom, name: currentName, value: v }).then(()=> {
        yourVote.textContent = v;
        renderVotes(v);
      });
    };
    voteGrid.appendChild(btn);
  });
}

function setHostLinkUI() {
  const hostFlag = localStorage.getItem("host:" + currentRoom) === "1";
  isHost = hostFlag;
  revealBtn.style.display = hostFlag ? "" : "none";
  resetBtn.style.display = hostFlag ? "" : "none";
  hostHint.textContent = hostFlag ? "You are the host of room " + currentRoom + "." : "";
}

function connectEvents() {
  if (SSE) SSE.close();
  // Include the current user's name so the server can tailor snapshots if needed
  const nameParam = currentName ? "&name=" + encodeURIComponent(currentName) : "";
  SSE = new EventSource("/events?room="+encodeURIComponent(currentRoom) + nameParam);
  SSE.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "update") updateUI(msg.data);
    } catch {}
  };
}

function updateUI(snap) {
  if (!snap) return;
  roomBar.style.display = "";
  voteCard.style.display = "";
  playersCard.style.display = "";

  roomCode.textContent = snap.room;
  hostName.textContent = snap.host;
  statusText.textContent = snap.revealed ? "Revealed" : "Voting";

  // Players
  playersTbody.innerHTML = "";
  let myVote = "None";
  (snap.players || []).forEach((p) => {
    const tr = document.createElement('tr');
    const voted = p.vote !== null;
    const shown = snap.revealed ? (p.vote ?? "") : (voted ? "✅" : "—");
    tr.innerHTML = `<td>${p.name}</td><td>${voted ? "Yes" : "No"}</td><td>${shown}</td>`;
    playersTbody.appendChild(tr);
    if (p.name === currentName) myVote = p.vote ?? "None";
  });
  yourVote.textContent = myVote;
  renderVotes(myVote === "None" ? null : myVote);

  // Tally
  if (snap.revealed) {
    tallyCard.style.display = "";
    tallyTbody.innerHTML = "";
    const entries = Object.entries(snap.tally).sort((a,b)=>a[0].localeCompare(b[0]));
    if (entries.length===0) {
      tallyTbody.innerHTML = "<tr><td colspan=2 class='muted'>No votes</td></tr>";
    } else {
      entries.forEach(([val, cnt])=>{
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${val}</td><td>${cnt}</td>`;
        tallyTbody.appendChild(tr);
      });
    }
  } else {
    tallyCard.style.display = "none";
  }
}

// Buttons
document.getElementById('joinBtn').onclick = () => {
  const n = nameEl.value.trim();
  const r = roomEl.value.trim().toUpperCase();
  if (!n || !r) { alert("Enter your name and a room code."); return; }
  jfetch("/api/join", { room: r, name: n }).then(resp => {
    if (resp.error) { alert(resp.error); return; }
    currentRoom = r; currentName = n;
    setHostLinkUI();
    connectEvents();
    jfetch("/api/state", { room: r }).then(s => updateUI(s.data));
  });
};

document.getElementById('createBtn').onclick = () => {
  const n = nameEl.value.trim();
  if (!n) { alert("Enter a name to host."); return; }
  jfetch("/api/create_room", { host: n }).then(resp => {
    if (resp.error) { alert(resp.error); return; }
    currentRoom = resp.room;
    currentName = n;
    roomEl.value = currentRoom;
    localStorage.setItem("host:" + currentRoom, "1");
    setHostLinkUI();
    connectEvents();
    jfetch("/api/state", { room: currentRoom }).then(s => updateUI(s.data));
  });
};

document.getElementById('revealBtn').onclick = () => jfetch("/api/reveal", { room: currentRoom, name: currentName });
document.getElementById('resetBtn').onclick  = () => jfetch("/api/reset",  { room: currentRoom, name: currentName });
document.getElementById('copyBtn').onclick   = async () => {
  const url = location.origin + "/?room=" + encodeURIComponent(currentRoom);
  await navigator.clipboard.writeText(url);
  const btn = document.getElementById('copyBtn');
  btn.textContent = "Copied!";
  setTimeout(()=> btn.textContent = "Copy link", 1200);
};

// Auto-fill from URL
if (qs.get("room")) {
  roomEl.value = qs.get("room").toUpperCase();
}
