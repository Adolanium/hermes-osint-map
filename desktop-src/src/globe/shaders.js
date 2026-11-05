// GLSL for the OSINT globe. Conventions: unit sphere, point(lat, lon) = (cos φ sin λ, sin φ, cos φ cos λ),
// the country raster's row 0 is 90°N and column 0 is 180°W. Every fragment shader outputs
// premultiplied alpha.

const COMMON = /* glsl */ `
#define PI 3.141592653589793
// 8x8 ordered (Bayer) threshold in [0, 1), from bit-interleaving x and x^y.
float bayer8(vec2 p) {
  ivec2 i = ivec2(mod(p, 8.0));
  int x = i.x;
  int z = i.x ^ i.y;
  int v = ((z & 1) << 5) | ((x & 1) << 4) | ((z & 2) << 2) | ((x & 2) << 1) | ((z & 4) >> 1) | ((x & 4) >> 2);
  return (float(v) + 0.5) / 64.0;
}
// Either a hard dithered step or a smooth level, by uDither.
float tone(float level, float threshold, float dither) {
  return mix(clamp(level, 0.0, 1.0), step(threshold, level), dither);
}
`

export const earthVertex = /* glsl */ `
out vec3 vObj;
out vec3 vNormalV;
out vec3 vPosV;
void main() {
  vObj = position;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vPosV = mv.xyz;
  vNormalV = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * mv;
}
`

export const earthFragment = /* glsl */ `
precision highp float;
precision highp int;
${COMMON}
uniform sampler2D uIds;
uniform sampler2D uHeat;
uniform ivec2 uIdSize;
uniform float uHover;
uniform float uSelected;
uniform float uFocus;
uniform float uDither;
uniform float uCell;
uniform float uDark;
uniform vec3 uLight;
uniform vec3 uOcean;
uniform vec3 uOceanLit;
uniform vec3 uLandLow;
uniform vec3 uLand;
uniform vec3 uAccent;
uniform vec3 uHeatColor;
uniform vec3 uRim;
in vec3 vObj;
in vec3 vNormalV;
in vec3 vPosV;

float idAt(ivec2 t) {
  t.x = ((t.x % uIdSize.x) + uIdSize.x) % uIdSize.x;
  t.y = clamp(t.y, 0, uIdSize.y - 1);
  return floor(texelFetch(uIds, t, 0).r * 255.0 + 0.5);
}

float gridLine(float value, float spacing) {
  float d = abs(fract(value / spacing + 0.5) - 0.5) * spacing;
  float w = fwidth(value);
  return 1.0 - smoothstep(0.0, w * 1.2, d);
}

void main() {
  vec3 n = normalize(vObj);
  float lat = asin(clamp(n.y, -1.0, 1.0));
  float lon = atan(n.x, n.z);
  vec2 uv = vec2((lon + PI) / (2.0 * PI), (0.5 * PI - lat) / PI);
  vec2 tc = uv * vec2(uIdSize) - 0.5;
  ivec2 b = ivec2(floor(tc));
  vec2 f = fract(tc);
  float a = idAt(b);
  float c1 = idAt(b + ivec2(1, 0));
  float c2 = idAt(b + ivec2(0, 1));
  float c3 = idAt(b + ivec2(1, 1));
  float land = mix(mix(step(0.5, a), step(0.5, c1), f.x), mix(step(0.5, c2), step(0.5, c3), f.x), f.y);
  float id = f.y < 0.5 ? (f.x < 0.5 ? a : c1) : (f.x < 0.5 ? c2 : c3);
  float seam = (a != c1 || a != c2 || a != c3) ? 1.0 : 0.0;
  // Only seams between two countries are borders; coastline seams are handled by 'land'.
  float allLand = step(0.5, a) * step(0.5, c1) * step(0.5, c2) * step(0.5, c3);
  float border = seam * allLand;

  vec3 nv = normalize(vNormalV);
  vec3 toEye = normalize(-vPosV);
  float facing = max(dot(nv, toEye), 0.0);
  float rim = pow(1.0 - facing, 2.6);
  float lit = smoothstep(-0.35, 1.0, dot(nv, normalize(uLight)));
  float threshold = bayer8(floor(gl_FragCoord.xy / uCell));

  // Graticule: 15 degree lines, fading out toward the poles' convergence.
  float latDeg = degrees(lat);
  float lonDeg = degrees(lon);
  float lonWrapped = degrees(atan(-n.x, -n.z));
  float lonGrid = fwidth(lonDeg) > 90.0 ? gridLine(lonWrapped, 15.0) : gridLine(lonDeg, 15.0);
  float grid = max(gridLine(latDeg, 15.0), lonGrid * (1.0 - smoothstep(70.0, 86.0, abs(latDeg))));

  float oceanLevel = 0.015 + 0.2 * lit * lit * lit + 0.34 * rim * rim + 0.2 * grid;
  vec3 ocean = mix(uOcean, uOceanLit, tone(oceanLevel, threshold, uDither));

  bool country = id > 0.5 && id < 254.5;
  float heat = country ? texture(uHeat, vec2((id + 0.5) / 256.0, 0.5)).r : 0.0;
  float selected = country && abs(id - uSelected) < 0.5 ? 1.0 : 0.0;
  float hovered = country && abs(id - uHover) < 0.5 ? 1.0 : 0.0;
  float landLevel = 0.30 + 0.70 * lit + 0.18 * rim;
  landLevel *= mix(1.0, 0.62, uFocus * (1.0 - selected));
  vec3 landColor = mix(uLandLow, uLand, tone(landLevel, threshold, uDither));
  landColor = mix(landColor, uAccent, selected * (0.42 + 0.28 * tone(lit, threshold, uDither)) + hovered * 0.22);
  // Heat glows rather than floods: capped, and dithered into "hot pixels" in dither mode.
  float heatTone = mix(heat * 0.62, step(threshold, heat * 0.95) * 0.78, uDither);
  landColor = mix(landColor, uHeatColor, heatTone);
  landColor = mix(landColor, mix(uOcean, uAccent, selected), border * (0.55 + 0.25 * selected));

  vec3 color = mix(ocean, landColor, land);
  // Atmospheric rim light inside the limb.
  color = mix(color, uRim, rim * mix(0.32, 0.5, uDark) * tone(0.35 + rim, threshold, uDither * 0.6));
  gl_FragColor = vec4(color, 1.0);
}
`

export const backdropVertex = /* glsl */ `
out vec2 vPos;
void main() {
  vPos = position.xy;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`

export const backdropFragment = /* glsl */ `
precision highp float;
precision highp int;
${COMMON}
uniform vec2 uCenter;
uniform float uRadius;
uniform vec3 uGlow;
uniform vec3 uStar;
uniform float uTime;
uniform float uDither;
uniform float uCell;
uniform float uDark;
uniform float uMotion;
in vec2 vPos;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

void main() {
  vec2 frag = gl_FragCoord.xy;
  float d = length(frag - uCenter);
  float x = (d - uRadius) / uRadius;
  float threshold = bayer8(floor(frag / uCell));
  // Halo: a tight bright band at the limb plus a wide soft falloff.
  // A tight limb band (dithered in dither mode) over a wide, faint, always-smooth glow.
  float band = x > 0.0 ? exp(-x * 34.0) * 0.7 : 0.0;
  float wide = x > 0.0 ? exp(-x * 4.5) * 0.10 : 0.0;
  float haloA = mix(band, step(threshold, band) * 0.6, uDither * 0.35) + wide;
  haloA *= mix(0.7, 1.0, uDark);
  // Sparse stars outside the globe, twinkling slowly.
  vec2 cell = floor(frag / uCell);
  float h = hash(cell);
  float star = step(0.9991, h) * smoothstep(0.05, 0.5, x);
  star *= 0.35 + 0.65 * (0.5 + 0.5 * sin(uTime * mix(0.0, 0.9, uMotion) + h * 400.0));
  star *= mix(0.45, 0.8, uDark);
  float a = clamp(haloA + star, 0.0, 1.0);
  vec3 color = (uGlow * haloA + uStar * star) / max(a, 1e-4);
  gl_FragColor = vec4(color * a, a);
}
`

export const markerVertex = /* glsl */ `
precision highp float;
in vec3 aPos;      // world position (unit sphere, altitude included)
in vec4 aStyle;    // shape, colour slot, size (CSS px), heading (radians, 0 = north, clockwise)
in vec4 aMeta;     // alpha (age fade), birth time (s), layer slot, flags (1 = selected)
uniform vec2 uRes;
uniform float uPixelRatio;
uniform float uTime;
uniform float uMask[12];
uniform float uZoom;
out vec2 vUv;
out float vShape;
out float vColor;
out float vAlpha;
out float vAge;
out float vSelected;
out float vSizePx;

void main() {
  int layer = int(aMeta.z + 0.5);
  float on = 0.0;
  for (int i = 0; i < 12; i++) if (i == layer) on = uMask[i];
  vUv = position.xy * 2.0;
  vShape = aStyle.x;
  vColor = aStyle.y;
  vAlpha = aMeta.x;
  vAge = uTime - aMeta.y;
  vSelected = aMeta.w;
  if (on < 0.5 || aMeta.x <= 0.0) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    return;
  }
  mat4 mvp = projectionMatrix * modelViewMatrix;
  vec4 cp = mvp * vec4(aPos, 1.0);
  // Screen direction of north at this point, so headings read correctly anywhere on the globe.
  vec3 n = normalize(aPos);
  vec3 north = vec3(0.0, 1.0, 0.0) - n * n.y;
  north = north / max(length(north), 1e-4);
  vec4 cn = mvp * vec4(aPos + north * 0.01, 1.0);
  vec2 up = (cn.xy / cn.w - cp.xy / cp.w) * uRes;
  float len = length(up);
  up = mix(vec2(0.0, 1.0), up / max(len, 1e-6), step(1e-5, len));
  float h = aStyle.w;
  vec2 dirUp = up * cos(h) + vec2(up.y, -up.x) * sin(h);   // rotate clockwise by heading
  vec2 dirRight = vec2(dirUp.y, -dirUp.x);
  bool oriented = aStyle.x > 3.5 && aStyle.x < 4.5;
  if (!oriented) {
    dirUp = vec2(0.0, 1.0);
    dirRight = vec2(1.0, 0.0);
  }
  float sizePx = aStyle.z * uZoom * (aMeta.w > 0.5 ? 1.35 : 1.0);
  // Room around the shape for the glow and the "new" ring.
  float pad = 2.6;
  vSizePx = sizePx;
  vec2 corner = (dirRight * position.x + dirUp * position.y) * sizePx * pad * uPixelRatio;
  gl_Position = vec4(cp.x + corner.x * 2.0 / uRes.x * cp.w, cp.y + corner.y * 2.0 / uRes.y * cp.w, cp.z, cp.w);
}
`

export const markerFragment = /* glsl */ `
precision highp float;
uniform vec3 uColors[12];
uniform vec3 uInk;
uniform float uDark;
uniform float uStatic;
in vec2 vUv;
in float vShape;
in float vColor;
in float vAlpha;
in float vAge;
in float vSelected;
in float vSizePx;

// A "norm" whose level set 1.0 is the shape's outline (so one formula draws every shape).
float shapeNorm(vec2 p, int s) {
  if (s == 0) return length(p);                                                    // circle
  if (s == 1) return abs(p.x) + abs(p.y);                                          // diamond
  if (s == 2) return max(-p.y * 2.0, max(dot(p, vec2(0.866, 0.5)), dot(p, vec2(-0.866, 0.5))) * 2.0) * 0.8;  // triangle
  if (s == 3) return max(abs(p.x), abs(p.y)) * 1.1;                                 // square
  if (s == 4) {                                                                     // dart (aircraft), points along heading
    vec2 q = p * vec2(1.8, 1.0);
    return max(-q.y * 2.0, max(dot(q, vec2(0.866, 0.5)), dot(q, vec2(-0.866, 0.5))) * 2.0) * 0.8;
  }
  if (s == 5) {                                                                     // plus (satellite)
    vec2 q = abs(p);
    return min(max(q.x * 3.2, q.y), max(q.y * 3.2, q.x)) * 1.05;
  }
  if (s == 6) {                                                                     // hexagon
    vec2 q = abs(p);
    return max(q.x * 0.866 + q.y * 0.5, q.y) * 1.12;
  }
  if (s == 8) return max(abs(p.x), abs(p.y)) * 1.15;                                // 8: frame (hollow square)
  return length(p);                                                                 // 7: ring (drawn hollow)
}

void main() {
  int s = int(vShape + 0.5);
  float pad = 2.6;
  vec2 p = vUv * pad;                     // shape units: outline at norm 1
  float n = shapeNorm(p, s);
  float aa = fwidth(n) * 1.1;
  vec3 color = uColors[int(vColor + 0.5)];
  float fill = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, n);
  if (s == 7 || s == 8) fill *= smoothstep(0.62 - aa, 0.62 + aa, n);  // hollow ring / frame
  float edge = (1.0 - smoothstep(0.0, aa * 1.6, abs(n - 1.0))) * 0.9;  // crisp outline
  float glow = exp(-max(n - 1.0, 0.0) * 2.4) * (1.0 - fill) * mix(0.18, 0.4, uDark);
  // "Just arrived": an expanding ring for the first 2.4 s.
  float ringR = 1.1 + vAge * 0.75;
  float ring = vAge >= 0.0 && vAge < 2.4 && uStatic < 0.5
    ? (1.0 - smoothstep(0.0, 0.18, abs(n - ringR))) * (1.0 - vAge / 2.4) * 0.9 : 0.0;
  // Selected: a ring in ink colour.
  float sel = vSelected > 0.5 ? (1.0 - smoothstep(0.0, 0.14, abs(n - 1.9))) : 0.0;
  vec3 rgb = color;
  float a = max(fill * 0.92, edge) + glow + ring;
  vec3 outlineMix = mix(color, uInk, 0.35 * edge * (1.0 - fill));
  rgb = mix(outlineMix, uInk, sel);
  a = clamp(max(a, sel), 0.0, 1.0) * vAlpha;
  if (a < 0.004) discard;
  gl_FragColor = vec4(rgb * a, a);
}
`

export const pathVertex = /* glsl */ `
precision highp float;
in float aT;
out float vT;
void main() {
  vT = aT;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`

export const pathFragment = /* glsl */ `
precision highp float;
uniform vec3 uColor;
in float vT;
void main() {
  float a = 0.15 + 0.6 * (1.0 - vT);
  gl_FragColor = vec4(uColor * a, a);
}
`

// --- Cyber attacks layer: travelling arcs and impact rings (from Hermes Threat Map) -----------------

export const arcVertex = /* glsl */ `
precision highp float;
#define PI 3.141592653589793
in float aT;
in float aSide;
in vec3 aSrc;
in vec3 aDst;
in vec4 aTiming;   // start (s), duration (s), category, seed
uniform float uTime;
uniform vec2 uRes;
uniform float uWidth;   // core half-width in device pixels
uniform vec4 uMask;
uniform float uHighlight;
out float vT;
out float vSide;
out float vLife;
out float vKind;
out float vLen;
out float vHi;

// Written straight-line, without helper functions or ternaries on vectors: ANGLE's D3D backend
// mistranslated an earlier version (a helper called three times returned the same point).
void main() {
  float ang = acos(clamp(dot(aSrc, aDst), -1.0, 1.0));
  float life = (uTime - aTiming.x) / aTiming.y;
  int kind = int(aTiming.z + 0.5);
  float visible = kind == 0 ? uMask.x : kind == 1 ? uMask.y : kind == 2 ? uMask.z : uMask.w;
  vT = aT;
  vSide = aSide;
  vLife = life;
  vKind = aTiming.z;
  vLen = ang;
  // Locals only: reading back a vertex 'out' is not portable across GPU backends (ANGLE/D3D).
  float hi = float(gl_InstanceID) == uHighlight ? 1.0 : 0.0;
  vHi = hi;
  if (life < 0.0 || life > 1.0 || visible < 0.5) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    return;
  }
  // Point on the arc and its tangent. u(t) is the great circle from aSrc to aDst (unit length, so
  // no normalize), r(t) the height above the surface. A same-place attack (one geolocation for
  // both ends) becomes a short vertical spike.
  float t = aT;
  float same = step(ang, 0.006);
  float s = max(sin(ang), 1e-4);
  vec3 u = mix((sin((1.0 - t) * ang) * aSrc + sin(t * ang) * aDst) / s, aSrc, same);
  vec3 du = mix(ang * (-cos((1.0 - t) * ang) * aSrc + cos(t * ang) * aDst) / s, vec3(0.0), same);
  float lift = mix(0.025 + 0.36 * ang / PI, 0.085, same);
  float r = 1.002 + lift * sin(PI * t);
  float dr = lift * PI * cos(PI * t);
  vec3 p = u * r;
  vec3 dp = du * r + u * dr;
  mat4 mvp = projectionMatrix * modelViewMatrix;
  vec4 cp = mvp * vec4(p, 1.0);
  vec4 cq = mvp * vec4(p + dp * 0.01, 1.0);
  // Screen-space direction, without normalize(0) anywhere (no NaN can reach the position).
  vec2 dir = (cq.xy / cq.w - cp.xy / cp.w) * uRes;
  float len = length(dir);
  dir = mix(vec2(1.0, 0.0), dir / max(len, 1e-6), step(1e-5, len));
  // Not "normal": that name is three's built-in vertex attribute, and shadowing it is not portable.
  vec2 across = vec2(-dir.y, dir.x);
  float outer = uWidth * 3.2 * (hi > 0.5 ? 1.6 : 1.0);
  // A fresh vec4 rather than 'cp.xy += ...': the swizzle compound assignment was dropped by
  // ANGLE's D3D backend.
  vec2 offset = across * (aSide * outer * cp.w) * (2.0 / uRes);
  gl_Position = vec4(cp.x + offset.x, cp.y + offset.y, cp.z, cp.w);
}
`

export const arcFragment = /* glsl */ `
precision highp float;
uniform vec3 uColors[4];
uniform float uWidth;
uniform float uStatic;
uniform float uDark;
uniform float uHighlight;
in float vT;
in float vSide;
in float vLife;
in float vKind;
in float vLen;
in float vHi;

void main() {
  int kind = int(vKind + 0.5);
  float outer = uWidth * 3.2 * (vHi > 0.5 ? 1.6 : 1.0);
  float d = abs(vSide) * outer;   // pixels from the centre line

  // Travelling head: reaches the target at life = 1/1.3, then the tail drains into it.
  float head = vLife * 1.3;
  float tailLength = 0.34;
  float x = (head - vT) / tailLength;
  float trail = (vT <= head && x <= 1.0) ? pow(max(1.0 - x, 0.0), 1.7) : 0.0;
  float hd = (head - vT) * 22.0;
  float headGlow = exp(-hd * hd) * step(vT, head + 0.02) * step(head, 1.04);
  float fadeIn = smoothstep(0.0, 0.08, vLife);
  float fadeOut = 1.0 - smoothstep(0.78, 1.0, vLife);
  if (uStatic > 0.5) {
    trail = 0.75;
    headGlow = 0.0;
  }
  float path = 0.16 * fadeIn * fadeOut;

  // Category pattern along the arc: exploit solid, malware dashed, phishing dotted.
  float segments = max(7.0, vLen * 42.0);
  float u = fract(vT * segments);
  float pattern = kind == 1 ? step(u, 0.62) : kind == 2 ? 1.0 - smoothstep(0.16, 0.3, abs(u - 0.5)) : 1.0;

  float width = uWidth * mix(0.55, 1.25, trail) + headGlow * uWidth * 0.8;
  float core = 1.0 - smoothstep(width - 0.8, width + 0.8, d);
  float glow = exp(-d / max(1.0, width * 1.9)) * (trail * 0.55 + headGlow * 0.9);
  float intensity = core * max(trail, path) * mix(1.0, pattern, step(0.02, 1.0 - headGlow)) + glow;
  float alpha = clamp(intensity * fadeOut, 0.0, 1.0);
  if (uHighlight >= 0.0) alpha *= vHi > 0.5 ? 1.5 : 0.28;
  vec3 color = uColors[kind];
  color = mix(color, vec3(1.0), headGlow * 0.55 * uDark);
  if (alpha < 0.003) discard;
  gl_FragColor = vec4(color * alpha, alpha);
}
`

export const impactVertex = /* glsl */ `
precision highp float;
in vec3 aPos;
in vec4 aTiming;  // start, duration, category, role (0 target, 1 source)
in float aScale;
uniform float uTime;
uniform vec4 uMask;
out vec2 vUv;
out float vLife;
out float vKind;
out float vRole;
void main() {
  float life = (uTime - aTiming.x) / aTiming.y;
  int kind = int(aTiming.z + 0.5);
  float visible = kind == 0 ? uMask.x : kind == 1 ? uMask.y : kind == 2 ? uMask.z : uMask.w;
  vLife = life;
  vKind = aTiming.z;
  vRole = aTiming.w;
  vUv = position.xy * 2.0;
  if (life < 0.0 || life > 1.0 || visible < 0.5) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    return;
  }
  vec3 n = normalize(aPos);
  vec3 up = abs(n.y) > 0.98 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
  vec3 east = normalize(cross(up, n));
  vec3 north = cross(n, east);
  float size = aScale * (aTiming.w > 0.5 ? 0.032 : 0.07);
  vec3 world = n * 1.003 + (east * position.x + north * position.y) * size;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(world, 1.0);
}
`

export const impactFragment = /* glsl */ `
precision highp float;
uniform vec3 uColors[4];
uniform float uStatic;
uniform float uDark;
in vec2 vUv;
in float vLife;
in float vKind;
in float vRole;

// A "norm" whose level set 1.0 is the category's outline, so one ring formula draws every shape.
float shapeNorm(vec2 p, int kind) {
  if (kind == 0) return abs(p.x) + abs(p.y);                         // diamond
  if (kind == 1) return length(p);                                   // circle
  if (kind == 2) {                                                   // triangle, point up
    return max(-p.y * 2.0, max(dot(p, vec2(0.866, 0.5)), dot(p, vec2(-0.866, 0.5))) * 2.0) * 0.82;
  }
  return max(abs(p.x), abs(p.y)) * 1.12;                              // square
}

void main() {
  int kind = int(vKind + 0.5);
  float n = shapeNorm(vUv, kind);
  float aa = fwidth(n) * 1.2;
  float life = clamp(vLife, 0.0, 1.0);
  float ease = 1.0 - pow(1.0 - life, 3.0);
  float alpha;
  if (uStatic > 0.5) {
    float outline = 1.0 - smoothstep(0.05, 0.05 + aa, abs(n - 0.45));
    alpha = (outline * 0.9 + (1.0 - smoothstep(0.18, 0.18 + aa, n)) * 0.7) * (1.0 - smoothstep(0.6, 1.0, life));
  } else if (vRole > 0.5) {
    float r = 0.25 + 0.7 * ease;
    alpha = (1.0 - smoothstep(0.05, 0.05 + aa, abs(n - r))) * (1.0 - life) * 0.75;
  } else {
    float r1 = 0.12 + 0.86 * ease;
    float r2 = 0.86 * (1.0 - pow(1.0 - clamp((life - 0.16) / 0.84, 0.0, 1.0), 3.0));
    float ring1 = (1.0 - smoothstep(0.035, 0.035 + aa, abs(n - r1))) * (1.0 - life);
    float ring2 = (1.0 - smoothstep(0.025, 0.025 + aa, abs(n - r2))) * (1.0 - life) * 0.55 * step(0.16, life);
    float core = (1.0 - smoothstep(0.16, 0.16 + aa, n)) * (1.0 - smoothstep(0.0, 0.55, life));
    float flash = exp(-n * 5.0) * (1.0 - smoothstep(0.0, 0.25, life)) * 0.8;
    alpha = ring1 + ring2 + core + flash;
  }
  alpha = clamp(alpha, 0.0, 1.0);
  if (alpha < 0.003) discard;
  vec3 color = mix(uColors[kind], vec3(1.0), (1.0 - smoothstep(0.0, 0.3, life)) * 0.35 * uDark * (1.0 - vRole));
  gl_FragColor = vec4(color * alpha, alpha);
}
`
