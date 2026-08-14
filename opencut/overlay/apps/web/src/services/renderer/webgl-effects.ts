/**
 * WebGL2 fallback for Shamrock effect shaders the published opencut-wasm
 * binary does not yet know (it only ships gaussian-blur).
 * Same EffectUniforms layout: resolution, unused vec2, scalars.xyzw
 */

import type { EffectPass, EffectUniformValue } from "@/effects/types";

const VERT = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  v_uv.y = 1.0 - v_uv.y;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const FRAG_LIB = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;
uniform sampler2D u_texture;
uniform vec2 u_resolution;
uniform vec4 u_scalars;

`;

const SHADERS: Record<string, string> = {
	"color-grade": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float brightness = u_scalars.x;
  float contrast = u_scalars.y;
  float saturation = u_scalars.z;
  float temperature = u_scalars.w;
  vec3 rgb = c.rgb + brightness;
  rgb = (rgb - 0.5) * (1.0 + contrast) + 0.5;
  float luma = dot(rgb, vec3(0.2126, 0.7152, 0.0722));
  rgb = mix(vec3(luma), rgb, 1.0 + saturation);
  rgb.r += temperature * 0.08;
  rgb.b -= temperature * 0.08;
  outColor = vec4(clamp(rgb, 0.0, 1.0), c.a);
}`,
	vignette: `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float intensity = u_scalars.x;
  float softness = max(u_scalars.y, 0.05);
  vec2 p = v_uv * 2.0 - 1.0;
  p.x *= u_resolution.x / max(u_resolution.y, 1.0);
  float d = length(p);
  float v = smoothstep(softness, softness - 0.85, d * intensity);
  outColor = vec4(c.rgb * v, c.a);
}`,
	"film-grain": `${FRAG_LIB}
float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float amount = u_scalars.x;
  float size = max(u_scalars.y, 0.5);
  vec2 g = floor(v_uv * u_resolution / size);
  float n = hash(g) * 2.0 - 1.0;
  outColor = vec4(clamp(c.rgb + n * amount, 0.0, 1.0), c.a);
}`,
	chromatic: `${FRAG_LIB}
void main() {
  float amount = u_scalars.x;
  vec2 dir = (v_uv - 0.5) * amount;
  float r = texture(u_texture, v_uv + dir).r;
  float g = texture(u_texture, v_uv).g;
  float b = texture(u_texture, v_uv - dir).b;
  float a = texture(u_texture, v_uv).a;
  outColor = vec4(r, g, b, a);
}`,
	"glitch-rgb": `${FRAG_LIB}
void main() {
  float amount = u_scalars.x;
  float slice = step(0.92, fract(v_uv.y * 28.0 + u_scalars.y * 3.0));
  vec2 off = vec2(amount * (slice * 2.0 - 0.4), 0.0);
  float r = texture(u_texture, v_uv + off).r;
  float g = texture(u_texture, v_uv).g;
  float b = texture(u_texture, v_uv - off).b;
  outColor = vec4(r, g, b, texture(u_texture, v_uv).a);
}`,
	pixelate: `${FRAG_LIB}
void main() {
  float size = max(u_scalars.x, 1.0);
  vec2 grid = u_resolution / size;
  vec2 uv = (floor(v_uv * grid) + 0.5) / grid;
  outColor = texture(u_texture, uv);
}`,
	invert: `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float amount = u_scalars.x;
  outColor = vec4(mix(c.rgb, 1.0 - c.rgb, amount), c.a);
}`,
	posterize: `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float levels = max(u_scalars.x, 2.0);
  outColor = vec4(floor(c.rgb * levels) / levels, c.a);
}`,
	mirror: `${FRAG_LIB}
void main() {
  float mode = u_scalars.x;
  vec2 uv = v_uv;
  if (mode < 0.5) uv.x = abs(uv.x * 2.0 - 1.0);
  else if (mode < 1.5) uv.y = abs(uv.y * 2.0 - 1.0);
  else uv = abs(uv * 2.0 - 1.0);
  outColor = texture(u_texture, uv);
}`,
	sharpen: `${FRAG_LIB}
void main() {
  float amount = u_scalars.x;
  vec2 px = 1.0 / u_resolution;
  vec4 c = texture(u_texture, v_uv);
  vec4 blur = (
    texture(u_texture, v_uv + vec2(px.x, 0.0)) +
    texture(u_texture, v_uv - vec2(px.x, 0.0)) +
    texture(u_texture, v_uv + vec2(0.0, px.y)) +
    texture(u_texture, v_uv - vec2(0.0, px.y))
  ) * 0.25;
  outColor = vec4(clamp(c.rgb + (c.rgb - blur.rgb) * amount * 2.0, 0.0, 1.0), c.a);
}`,
	glow: `${FRAG_LIB}
void main() {
  float amount = u_scalars.x;
  vec2 px = 1.0 / u_resolution;
  vec4 c = texture(u_texture, v_uv);
  vec4 bloom = vec4(0.0);
  for (int i = -3; i <= 3; i++) {
    bloom += texture(u_texture, v_uv + vec2(px.x * float(i) * 2.0, 0.0));
    bloom += texture(u_texture, v_uv + vec2(0.0, px.y * float(i) * 2.0));
  }
  bloom /= 14.0;
  vec3 hi = max(bloom.rgb - 0.55, 0.0);
  outColor = vec4(clamp(c.rgb + hi * amount * 2.4, 0.0, 1.0), c.a);
}`,
	shake: `${FRAG_LIB}
void main() {
  float amount = u_scalars.x;
  float t = u_scalars.y;
  vec2 off = vec2(
    sin(t * 47.0) + sin(t * 19.0),
    cos(t * 31.0) + sin(t * 13.0)
  ) * amount;
  outColor = texture(u_texture, v_uv + off);
}`,
	"fade-black": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  outColor = vec4(mix(c.rgb, vec3(0.0), p), c.a);
}`,
	"fade-white": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  outColor = vec4(mix(c.rgb, vec3(1.0), p), c.a);
}`,
	"wipe-right": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  float edge = smoothstep(p - 0.02, p + 0.02, v_uv.x);
  outColor = vec4(c.rgb * edge, c.a);
}`,
	"wipe-left": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  float edge = smoothstep(p - 0.02, p + 0.02, 1.0 - v_uv.x);
  outColor = vec4(c.rgb * edge, c.a);
}`,
	"wipe-up": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  float edge = smoothstep(p - 0.02, p + 0.02, 1.0 - v_uv.y);
  outColor = vec4(c.rgb * edge, c.a);
}`,
	"wipe-down": `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  float edge = smoothstep(p - 0.02, p + 0.02, v_uv.y);
  outColor = vec4(c.rgb * edge, c.a);
}`,
	iris: `${FRAG_LIB}
void main() {
  vec4 c = texture(u_texture, v_uv);
  float p = clamp(u_scalars.x, 0.0, 1.0);
  vec2 d = v_uv - 0.5;
  d.x *= u_resolution.x / max(u_resolution.y, 1.0);
  float r = length(d);
  float open = mix(0.75, 0.0, p);
  float mask = smoothstep(open, open + 0.08, r);
  outColor = vec4(c.rgb * (1.0 - mask), c.a);
}`,
	"pixelize-out": `${FRAG_LIB}
void main() {
  float p = clamp(u_scalars.x, 0.0, 1.0);
  float size = mix(1.0, 48.0, p);
  vec2 grid = u_resolution / size;
  vec2 uv = (floor(v_uv * grid) + 0.5) / grid;
  vec4 c = texture(u_texture, uv);
  outColor = vec4(mix(c.rgb, vec3(0.0), p * 0.35), c.a);
}`,
	"zoom-blur-out": `${FRAG_LIB}
void main() {
  float p = clamp(u_scalars.x, 0.0, 1.0);
  vec2 dir = v_uv - 0.5;
  vec4 acc = vec4(0.0);
  for (int i = 0; i < 8; i++) {
    float t = float(i) / 7.0;
    acc += texture(u_texture, v_uv - dir * t * p * 0.35);
  }
  acc /= 8.0;
  outColor = vec4(mix(acc.rgb, vec3(0.0), p * 0.4), acc.a);
}`,
};

const WASM_ONLY = new Set(["gaussian-blur"]);

export function needsWebglFallback(passes: EffectPass[]): boolean {
	return passes.some((p) => !WASM_ONLY.has(p.shader) && SHADERS[p.shader]);
}

function num(value: EffectUniformValue | undefined, fallback = 0): number {
	if (typeof value === "number") return value;
	if (Array.isArray(value) && value.length) return value[0];
	return fallback;
}

function scalarsFor(pass: EffectPass): [number, number, number, number] {
	const u = pass.uniforms;
	switch (pass.shader) {
		case "color-grade":
			return [
				num(u.u_brightness),
				num(u.u_contrast),
				num(u.u_saturation),
				num(u.u_temperature),
			];
		case "vignette":
			return [num(u.u_intensity, 0.7), num(u.u_softness, 1.2), 0, 0];
		case "film-grain":
			return [num(u.u_amount, 0.12), num(u.u_size, 1.5), 0, 0];
		case "chromatic":
		case "glitch-rgb":
		case "shake":
			return [num(u.u_amount, 0.006), num(u.u_time, 0), 0, 0];
		case "pixelate":
			return [num(u.u_size, 12), 0, 0, 0];
		case "invert":
		case "sharpen":
		case "glow":
			return [num(u.u_amount, 0.5), 0, 0, 0];
		case "posterize":
			return [num(u.u_levels, 5), 0, 0, 0];
		case "mirror":
			return [num(u.u_mode, 0), 0, 0, 0];
		case "fade-black":
		case "fade-white":
		case "wipe-right":
		case "wipe-left":
		case "wipe-up":
		case "wipe-down":
		case "iris":
		case "pixelize-out":
		case "zoom-blur-out":
			return [num(u.u_progress, 0), 0, 0, 0];
		default:
			return [0, 0, 0, 0];
	}
}

export function applyWebglEffectPasses({
	source,
	width,
	height,
	passes,
}: {
	source: OffscreenCanvas;
	width: number;
	height: number;
	passes: EffectPass[];
}): OffscreenCanvas {
	let current: OffscreenCanvas = source;
	for (const pass of passes) {
		const frag = SHADERS[pass.shader];
		if (!frag) {
			continue;
		}
		current = runPass({
			source: current,
			width,
			height,
			frag,
			scalars: scalarsFor(pass),
		});
	}
	return current;
}

function runPass({
	source,
	width,
	height,
	frag,
	scalars,
}: {
	source: OffscreenCanvas;
	width: number;
	height: number;
	frag: string;
	scalars: [number, number, number, number];
}): OffscreenCanvas {
	const out = new OffscreenCanvas(width, height);
	const gl = out.getContext("webgl2", {
		premultipliedAlpha: false,
		preserveDrawingBuffer: true,
	});
	if (!gl) return source;

	const vs = compile(gl, gl.VERTEX_SHADER, VERT);
	const fs = compile(gl, gl.FRAGMENT_SHADER, frag);
	if (!vs || !fs) return source;
	const program = gl.createProgram();
	if (!program) return source;
	gl.attachShader(program, vs);
	gl.attachShader(program, fs);
	gl.linkProgram(program);
	if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
		console.warn("WebGL effect link failed", gl.getProgramInfoLog(program));
		return source;
	}

	const buf = gl.createBuffer();
	gl.bindBuffer(gl.ARRAY_BUFFER, buf);
	gl.bufferData(
		gl.ARRAY_BUFFER,
		new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
		gl.STATIC_DRAW,
	);
	const loc = gl.getAttribLocation(program, "a_pos");
	gl.enableVertexAttribArray(loc);
	gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

	const tex = gl.createTexture();
	gl.bindTexture(gl.TEXTURE_2D, tex);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
	gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
	gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0);
	gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);

	gl.viewport(0, 0, width, height);
	gl.useProgram(program);
	gl.uniform1i(gl.getUniformLocation(program, "u_texture"), 0);
	gl.uniform2f(gl.getUniformLocation(program, "u_resolution"), width, height);
	gl.uniform4f(
		gl.getUniformLocation(program, "u_scalars"),
		scalars[0],
		scalars[1],
		scalars[2],
		scalars[3],
	);
	gl.drawArrays(gl.TRIANGLES, 0, 6);
	return out;
}

function compile(
	gl: WebGL2RenderingContext,
	type: number,
	src: string,
): WebGLShader | null {
	const shader = gl.createShader(type);
	if (!shader) return null;
	gl.shaderSource(shader, src);
	gl.compileShader(shader);
	if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
		console.warn("WebGL effect compile failed", gl.getShaderInfoLog(shader));
		gl.deleteShader(shader);
		return null;
	}
	return shader;
}
