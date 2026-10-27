// The big screen follows `big` of the pushed view: the selected person's video (loop,
// muted, autoplay) while it is selected, read, shown or refused; the sim frame while
// the robot runs and whenever no video is on. Only registered videos are served.
"use strict";

function bigScreen() {
  const img = document.getElementById("frame");
  const clip = document.getElementById("clip");
  let playing = null; // the video id the <video> is on; never reloaded for the same id
  let simOn = true; // the frame poll runs only while the sim is on screen

  const refresh = () => {
    if (!simOn) { setTimeout(refresh, 250); return; }
    const next = new Image();
    next.onload = () => { img.src = next.src; setTimeout(refresh, 40); };
    next.onerror = () => setTimeout(refresh, 1000);
    next.src = `/frame.jpg?t=${Date.now()}`;
  };
  refresh();

  function showVideo(id) {
    if (playing !== id) {
      playing = id;
      clip.src = `/videos/${encodeURIComponent(id)}`;
    } else if (simOn) {
      clip.currentTime = 0; // back from the sim: the task from its beginning
    }
    simOn = false;
    img.hidden = true;
    clip.hidden = false;
    const p = clip.play();
    if (p && p.catch) p.catch(() => {});
  }

  function showSim() {
    simOn = true;
    clip.hidden = true;
    img.hidden = false;
    if (playing !== null) clip.pause();
  }

  follow((v) => {
    common(v);
    if (v.big && v.big.kind === "video" && v.big.video) showVideo(v.big.video);
    else showSim();
  });
}
