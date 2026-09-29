// Posture boxes belong to this analysed frame; there is no skeleton inference.
export function postureAllowed(role, mode, prompt) {
  return role === 'inside' && mode === 'objects' && String(prompt).split(',').some(x => ['person','persons'].includes(x.trim().toLowerCase()));
}

export function validPostureBox(box) {
  return Array.isArray(box) && box.length === 4 && box.every(value => Number.isFinite(value) && value >= 0 && value <= 1)
    && box[2] > box[0] && box[3] > box[1];
}

export function postureTtlMs(summary) {
  return summary?.engine === 'LocateAnything' ? 4000 : 1000;
}

export function postureFrameFresh(summary, elapsedMs = 0) {
  const age=summary?.age_ms ?? 0;
  return Boolean(summary?.status==='observed'&&Number.isFinite(elapsedMs)&&elapsedMs>=0
    &&Number.isFinite(age)&&age>=0&&age+elapsedMs<postureTtlMs(summary));
}

export function drawPosture(ctx, summary, rect) {
  if (!postureFrameFresh(summary)
    || !rect || ![rect.x, rect.y, rect.w, rect.h].every(Number.isFinite) || rect.w <= 0 || rect.h <= 0) return;
  const occupants = Array.isArray(summary.occupants) ? summary.occupants : [];
  ctx.save();ctx.setLineDash([]);ctx.font='600 11px "Segoe UI", sans-serif';
  for (const occupant of occupants) {
    if (!occupant || occupant.posture !== 'standing' || occupant.predicted || !validPostureBox(occupant.bbox)) continue;
    const [x0,y0,x1,y1]=occupant.bbox;
    const x=rect.x+x0*rect.w,y=rect.y+y0*rect.h,w=(x1-x0)*rect.w,h=(y1-y0)*rect.h;
    const colour='#ffbf77',label='Standing person';
    for (const [width,stroke] of [[5,'#11241ce6'],[2,colour]]) {
      ctx.lineWidth=width;ctx.strokeStyle=stroke;ctx.strokeRect(x,y,w,h);
    }
    const width=Math.min(rect.w,ctx.measureText(label).width+14);
    const labelX=Math.max(rect.x,Math.min(x,rect.x+rect.w-width));
    const labelY=Math.min(rect.y+rect.h-21,Math.max(rect.y,y+3));
    ctx.fillStyle='#11241cf2';ctx.fillRect(labelX,labelY,width,21);
    ctx.fillStyle=colour;ctx.fillText(label,labelX+7,labelY+14,Math.max(1,width-14));
  }
  ctx.restore();
}
