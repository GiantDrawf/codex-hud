import test from 'node:test';
import assert from 'node:assert/strict';

import {cardLayoutForColumns, displayColorForMode, progressBarWidthForCard} from './ink_hud.mjs';

test('uses full available width for limit cards when stacked', () => {
	const layout = cardLayoutForColumns(60);

	assert.equal(layout.wide, false);
	assert.equal(layout.cardWidth, 60);
});

test('keeps fixed card width when cards fit side by side', () => {
	const layout = cardLayoutForColumns(100);

	assert.equal(layout.wide, true);
	assert.equal(layout.cardWidth, 38);
});

test('expands progress bars to match wider stacked cards', () => {
	assert.equal(progressBarWidthForCard(60), 54);
});

test('drops accent colors in boss mode', () => {
	assert.equal(displayColorForMode(4, false), 'red');
	assert.equal(displayColorForMode(4, true), undefined);
});
