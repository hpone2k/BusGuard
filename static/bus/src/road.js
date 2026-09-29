import * as THREE from 'three';
import {roadOffset} from './travel-motion.js';

// Repeated instanced geometry supplies optic flow without moving the bus or
// rerendering its shadow map. All layers use the same presentation distance.
export function buildRoad(scene) {
  const root = new THREE.Group(); root.name = 'Singapore left-hand carriageway'; scene.add(root);
  const materials = new Map();
  const material = color => {
    if (!materials.has(color)) materials.set(color, new THREE.MeshStandardMaterial({
      color, roughness: 1, metalness: 0, envMapIntensity: .15,
    }));
    return materials.get(color);
  };
  function strip(name, width, height, depth, y, z, color) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material(color));
    mesh.name = name; mesh.position.set(0, y, z); mesh.receiveShadow = true; root.add(mesh); return mesh;
  }

  // Small deterministic grain, generated once; no asset downloads or per-frame
  // texture uploads. Two metres per repeat keeps the grain in world scale.
  const grainSize = 128, pixels = new Uint8Array(grainSize * grainSize * 4);
  let seed = 731;
  for (let i = 0; i < pixels.length; i += 4) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const value = 192 + (seed >>> 26);
    pixels[i] = pixels[i + 1] = pixels[i + 2] = value; pixels[i + 3] = 255;
  }
  const grain = new THREE.DataTexture(pixels, grainSize, grainSize, THREE.RGBAFormat);
  grain.colorSpace = THREE.SRGBColorSpace;
  grain.wrapS = grain.wrapT = THREE.RepeatWrapping;
  grain.repeat.set(90, 4.25); grain.anisotropy = 4;
  grain.magFilter = THREE.LinearFilter; grain.minFilter = THREE.LinearMipmapLinearFilter;
  grain.generateMipmaps = true; grain.needsUpdate = true;
  const asphalt = new THREE.Mesh(new THREE.PlaneGeometry(180, 8.5),
    new THREE.MeshStandardMaterial({color: '#303c35', map: grain, roughness: 1, metalness: 0, envMapIntensity: .05}));
  asphalt.name = 'Textured charcoal asphalt'; asphalt.rotation.x = -Math.PI / 2;
  asphalt.position.set(0, -.011, -1.05); asphalt.receiveShadow = true; root.add(asphalt);
  strip('Far road edge', 180, .004, .12, -.005, -5.08, '#edf0d9');
  strip('Kerb-side yellow line', 180, .004, .08, -.005, 2.88, '#e7c464');
  strip('Inner yellow line', 180, .004, .08, -.005, 2.68, '#e7c464');
  strip('Raised accessible pavement', 180, .16, 2.65, .05, 4.575, '#9aab94');
  strip('Pale stone kerb', 180, .18, .19, .06, 3.25, '#c0ccb6');
  strip('Far verge', 180, .055, 3.1, -.008, -6.86, '#63865c');

  const transform = new THREE.Matrix4();
  const details = [];
  function repeat(name, geometry, color, spacing, count, y, z, x = 0) {
    const mesh = new THREE.InstancedMesh(geometry, material(color), count); mesh.name = name;
    for (let i = 0; i < count; i++) {
      transform.makeTranslation((i - count / 2) * spacing + x, y, z); mesh.setMatrixAt(i, transform);
    }
    mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingSphere(); root.add(mesh);
    // Only the group translation changes. Instance matrices and geometry remain
    // static on the GPU, including during braking and long-running journeys.
    details.push({mesh, spacing});
    return mesh;
  }
  repeat('High-contrast lane dashes', new THREE.BoxGeometry(2.7, .008, .15), '#f1efdd', 6, 34, -.001, -1.85);
  repeat('Pavement cross joints', new THREE.BoxGeometry(.035, .004, 2.65), '#6c8166', 2, 94, .132, 4.575);
  strip('Pavement centre seam', 180, .004, .028, .132, 4.575, '#7d9174');
  repeat('Kerb block joints', new THREE.BoxGeometry(.035, .183, .193), '#72856d', 2, 94, .06, 3.25);
  repeat('White lane studs', new THREE.BoxGeometry(.12, .012, .17), '#f4f0ce', 6, 34, .005, -1.85, 3);
  repeat('Amber edge studs', new THREE.BoxGeometry(.10, .012, .12), '#dbb04a', 6, 34, .005, 2.46);

  const arrow = new THREE.Shape();
  arrow.moveTo(-1.15, -.095); arrow.lineTo(.28, -.095); arrow.lineTo(.28, -.38);
  arrow.lineTo(1.12, 0); arrow.lineTo(.28, .38); arrow.lineTo(.28, .095);
  arrow.lineTo(-1.15, .095); arrow.closePath();
  const arrowGeometry = new THREE.ShapeGeometry(arrow); arrowGeometry.rotateX(-Math.PI / 2);
  repeat('Forward lane arrows', arrowGeometry, '#eeeeda', 24, 10, .003, -.05, 9);
  repeat('Adjacent lane arrows', arrowGeometry, '#eeeeda', 24, 10, .003, -3.52, 9);

  // Landmarks beyond the carriageway add a second motion cue without crossing
  // the doors or the boarding pavement. They don't cast dynamic shadows.
  repeat('Verge delineator bases', new THREE.BoxGeometry(.22, .12, .22), '#6d7e64', 12, 18, .08, -5.7);
  repeat('Verge delineators', new THREE.BoxGeometry(.10, .66, .10), '#dce6d0', 12, 18, .42, -5.7);
  repeat('Delineator dark bands', new THREE.BoxGeometry(.105, .19, .105), '#34483b', 12, 18, .54, -5.7);
  repeat('Delineator reflectors', new THREE.BoxGeometry(.11, .075, .015), '#e9cd79', 12, 18, .57, -5.64);
  repeat('Slim avenue light poles', new THREE.CylinderGeometry(.042, .066, 4.5, 8), '#4e6254', 24, 10, 2.25, -6.15, 15);
  repeat('Avenue light arms', new THREE.BoxGeometry(.07, .07, 1.05), '#4e6254', 24, 10, 4.47, -5.66, 15);
  repeat('Avenue light housings', new THREE.BoxGeometry(.34, .09, .72), '#344c40', 24, 10, 4.47, -5.06, 15);
  repeat('Avenue light lenses', new THREE.BoxGeometry(.28, .012, .57), '#e3ebcf', 24, 10, 4.421, -5.06, 15);
  repeat('Avenue tree trunks', new THREE.CylinderGeometry(.12, .17, 1.95, 9), '#665e43', 18, 12, .975, -8.2, 4);
  const crown = new THREE.SphereGeometry(1, 12, 9); crown.scale(1.1, 1.4, 1.05);
  repeat('Avenue tree crowns', crown, '#3f714e', 18, 12, 2.65, -8.2, 4);
  const sideCrown = new THREE.SphereGeometry(.86, 12, 8); sideCrown.scale(1, 1.15, 1);
  repeat('Avenue tree lower crowns', sideCrown, '#527e4e', 18, 12, 2.17, -7.88, 3.3);

  function setTravel(distance) {
    const safeDistance = Number.isFinite(distance) ? distance : 0;
    for (const {mesh, spacing} of details) mesh.position.x = roadOffset(safeDistance, spacing);
    grain.offset.x = ((safeDistance / 2) % 1 + 1) % 1;
    root.userData.travelDistance = safeDistance;
  }
  setTravel(0);
  return {root, setTravel};
}
