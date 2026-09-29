import test from 'node:test';
import assert from 'node:assert/strict';
import {AnnouncementFeed} from '../static/announcement-feed.js';

const event = (id, message, at_ms = 1000) => ({id, message, at_ms});
const state = (events, vehicle = {}) => ({server_time_ms: 2000, vehicle, scenario: {
  announcement: events.at(-1), announcement_events: events,
}, announcements: [events.at(-1)?.message]});
test('a priority request followed by departure notice between polls is spoken in order', () => {
  const feed = new AnnouncementFeed();
  feed.take(state([event('1', 'Doors open.')]));
  const snapshot = state([event('1', 'Doors open.'), event('2', 'Please offer a priority seat.'), event('3', 'Please prepare for departure.')]);
  assert.deepEqual(feed.take(snapshot).map(x => x.id), ['2', '3']);
  assert.deepEqual(feed.take(snapshot), []);
});
test('connecting does not replay history and emergency guidance takes precedence', () => {
  const feed = new AnnouncementFeed();
  assert.deepEqual(feed.take(state([event('1','Old'), event('2','Now')])).map(x => x.id), ['2']);
  const held = state([event('3', 'Priority')], {emergency: true}); held.announcements = ['Emergency hold.'];
  assert.deepEqual(feed.take(held).map(x => x.message), ['Emergency hold.']);
  assert.deepEqual(feed.take(held), []);
});
test('old history and offline messages are not replayed', () => {
  const feed = new AnnouncementFeed(); feed.take(state([event('1', 'One')]));
  const next = state([event('2', 'Old'), event('3', 'Current', 49000)]); next.server_time_ms = 50000;
  assert.deepEqual(feed.take(next).map(x => x.id), ['3']);
  assert.deepEqual(feed.take(next, false), []);
  assert.deepEqual(feed.take(next).map(x => x.id), ['3']);
});
