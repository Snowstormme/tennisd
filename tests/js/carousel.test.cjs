const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const script = readFileSync('public/static/app.js', 'utf8');

// A deterministic event/timer boundary: execute the shipped script, without network or a real clock.
class Target {
  listeners = new Map();
  attributes = new Map();
  hidden = true;
  textContent = '';
  tabIndex = 0;
  classes = new Set();
  classList = {toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name)};
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  emit(name, event = {}) { for (const callback of this.listeners.get(name) || []) callback(event); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
}

function fixture({reduced = false, count = 3} = {}) {
  const carousel = new Target();
  const rotation = new Target();
  const status = new Target();
  const slides = Array.from({length: count}, () => new Target());
  const dots = Array.from({length: count}, () => new Target());
  carousel.querySelectorAll = selector => selector === '[data-featured-slide]' ? slides : dots;
  carousel.querySelector = selector => selector === '[data-featured-rotation]' ? rotation : status;
  const document = new Target();
  document.hidden = false;
  document.querySelectorAll = selector => selector === '[data-featured-carousel]' ? [carousel] : [];
  document.querySelector = () => null;
  const motion = new Target();
  motion.matches = reduced;
  const timers = new Map();
  let nextTimer = 0;
  const window = {
    matchMedia: () => motion,
    setInterval: callback => { timers.set(++nextTimer, callback); return nextTimer; },
    clearInterval: id => timers.delete(id),
  };
  runInNewContext(script, {document, window, navigator: {connection: {saveData: true}}});
  document.emit('DOMContentLoaded');
  return {carousel, rotation, status, slides, dots, document, motion, timers,
    tick() { for (const callback of [...timers.values()]) callback(); }};
}

test('automatic slides expose only the current link and do not announce unsolicited updates', () => {
  const f = fixture();
  assert.equal(f.timers.size, 1);
  f.tick();
  assert.equal(f.slides[0].tabIndex, -1);
  assert.equal(f.slides[0].attributes.get('aria-hidden'), 'true');
  assert.equal(f.slides[1].tabIndex, 0);
  assert.equal(f.slides[1].attributes.get('aria-hidden'), 'false');
  assert.equal(f.dots[1].attributes.get('aria-current'), 'true');
  assert.equal(f.dots[0].attributes.has('aria-current'), false);
  assert.equal(f.status.textContent, '');
});

test('Pause stays paused after hover exits; explicit Play resumes', () => {
  const f = fixture();
  f.carousel.emit('pointerenter');
  assert.equal(f.timers.size, 0);
  f.rotation.emit('click');
  f.carousel.emit('pointerleave');
  assert.equal(f.timers.size, 0);
  assert.equal(f.rotation.textContent, 'Play');
  f.rotation.emit('click');
  assert.equal(f.timers.size, 1);
});

test('keyboard focus pauses until explicit Play, including movement between carousel controls', () => {
  const f = fixture();
  f.carousel.emit('focusin', {target: f.slides[0]});
  f.carousel.emit('focusout', {relatedTarget: f.dots[0]});
  f.carousel.emit('focusin', {target: f.dots[0]});
  f.carousel.emit('pointerleave');
  assert.equal(f.timers.size, 0);
  f.carousel.emit('focusin', {target: f.rotation});
  f.rotation.emit('click');
  assert.equal(f.timers.size, 1);
});

test('manual selection stops rotation and announces its position', () => {
  const f = fixture();
  f.dots[2].emit('click');
  assert.equal(f.timers.size, 0);
  assert.equal(f.status.textContent, 'Match 3 of 3');
  assert.equal(f.slides[2].attributes.get('aria-hidden'), 'false');
  f.carousel.emit('pointerleave');
  f.tick();
  assert.equal(f.slides[2].tabIndex, 0);
});

test('hidden tabs stop timers and returning does not override a user pause', () => {
  const f = fixture();
  f.document.hidden = true;
  f.document.emit('visibilitychange');
  assert.equal(f.timers.size, 0);
  f.document.hidden = false;
  f.document.emit('visibilitychange');
  assert.equal(f.timers.size, 1);
  f.rotation.emit('click');
  f.document.hidden = true;
  f.document.emit('visibilitychange');
  f.document.hidden = false;
  f.document.emit('visibilitychange');
  assert.equal(f.timers.size, 0);
});

test('reduced motion prevents automatic start and preference changes stop an existing timer', () => {
  const f = fixture({reduced: true});
  assert.equal(f.timers.size, 0);
  assert.equal(f.rotation.textContent, 'Play');
  f.motion.matches = false;
  f.motion.emit('change');
  assert.equal(f.timers.size, 0);
  f.rotation.emit('click');
  assert.equal(f.timers.size, 1);
  f.motion.matches = true;
  f.motion.emit('change');
  assert.equal(f.timers.size, 0);
});

test('a single slide has no dead rotation control or timer', () => {
  const f = fixture({count: 1});
  assert.equal(f.timers.size, 0);
  assert.equal(f.rotation.hidden, true);
});
