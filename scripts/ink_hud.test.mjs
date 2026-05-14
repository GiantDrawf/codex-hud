import test from 'node:test';
import assert from 'node:assert/strict';

import {cardLayoutForColumns, displayColorForMode, progressBarWidthForCard} from './ink_hud.mjs';

test('shrinks cards to keep both limit windows on one row', () => {
	const layout = cardLayoutForColumns(60);

	assert.equal(layout.cardWidth, 29);
	assert.equal(layout.gap, 1);
});

test('keeps fixed card width on wide terminals', () => {
	const layout = cardLayoutForColumns(100);

	assert.equal(layout.cardWidth, 38);
	assert.equal(layout.gap, 1);
});

test('uses compact progress bars for narrow cards', () => {
	assert.equal(progressBarWidthForCard(29), 23);
});

test('drops accent colors in boss mode', () => {
	assert.equal(displayColorForMode(4, false), 'red');
	assert.equal(displayColorForMode(4, true), undefined);
});
