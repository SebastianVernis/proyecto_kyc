/* common.js — Argos OSINT animated background (shared across standalone pages) */
(function(){
  const wrap = document.querySelector('.argos-bg');
  if (!wrap) return;
  const cv = wrap.querySelector('canvas');
  const ctx = cv.getContext('2d');
  const quiet = wrap.classList.contains('argos-bg--quiet');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const still = quiet || reduce;
  const PLATA = '201,205,213', AMBAR = '232,180,74';
  let W, H, dpr, nodes = [], amber = [];
  function resize(){
    dpr = Math.min(devicePixelRatio || 1, 2);
    W = wrap.clientWidth; H = wrap.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    build();
  }
  function build(){
    const n = Math.round((W * H) / (quiet ? 44000 : 22000));
    nodes = [];
    for (let i = 0; i < n; i++){
      nodes.push({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - .5) * .12, vy: (Math.random() - .5) * .12,
        r: 1 + Math.random() * 1.4,
        a: .25 + Math.random() * .45
      });
    }
    amber = [];
    const spots = [[.18,.28],[.80,.22],[.72,.78]];
    spots.forEach(([fx,fy]) => amber.push({ x: W*fx + (Math.random()-.5)*80, y: H*fy + (Math.random()-.5)*80, t: Math.random()*Math.PI*2 }));
  }
  function centerFade(x, y){
    const dx = (x - W/2) / (W*.45), dy = (y - H/2) / (H*.45);
    const d = Math.min(1, Math.sqrt(dx*dx + dy*dy));
    return .25 + .75 * d * d;
  }
  const LINK = 150;
  function draw(t){
    ctx.clearRect(0, 0, W, H);
    for (let i = 0; i < nodes.length; i++){
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++){
        const b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y, d2 = dx*dx + dy*dy;
        if (d2 < LINK*LINK){
          const k = 1 - Math.sqrt(d2) / LINK;
          const f = centerFade((a.x+b.x)/2, (a.y+b.y)/2);
          ctx.strokeStyle = 'rgba('+PLATA+','+(k * .16 * f).toFixed(3)+')';
          ctx.lineWidth = .8;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
    for (const p of nodes){
      const f = centerFade(p.x, p.y);
      ctx.fillStyle = 'rgba('+PLATA+','+(p.a * f).toFixed(3)+')';
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI*2); ctx.fill();
      if (!still){
        p.x += p.vx; p.y += p.vy;
        if (p.x < -10) p.x = W + 10; if (p.x > W + 10) p.x = -10;
        if (p.y < -10) p.y = H + 10; if (p.y > H + 10) p.y = -10;
      }
    }
    for (const s of amber){
      const pulse = still ? .5 : (.5 + .5 * Math.sin(t * .0009 + s.t));
      nodes.forEach(p => {
        const dx = p.x - s.x, dy = p.y - s.y, d = Math.hypot(dx, dy);
        if (d < LINK * 1.3){
          ctx.strokeStyle = 'rgba('+AMBAR+','+(.10 + .12 * pulse) * (1 - d/(LINK*1.3))+')';
          ctx.lineWidth = .9;
          ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(p.x, p.y); ctx.stroke();
        }
      });
      const g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, 26);
      g.addColorStop(0, 'rgba('+AMBAR+','+(.22 + .18 * pulse).toFixed(3)+')');
      g.addColorStop(1, 'rgba('+AMBAR+',0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(s.x, s.y, 26, 0, Math.PI*2); ctx.fill();
      ctx.fillStyle = 'rgba('+AMBAR+','+(.75 + .25 * pulse).toFixed(3)+')';
      ctx.beginPath(); ctx.arc(s.x, s.y, 2.6, 0, Math.PI*2); ctx.fill();
    }
    if (!still) requestAnimationFrame(draw);
  }
  addEventListener('resize', () => { resize(); if (still) draw(0); });
  resize();
  still ? draw(0) : requestAnimationFrame(draw);
})();
