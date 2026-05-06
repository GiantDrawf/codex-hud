#!/usr/bin/env node

import React, {useCallback, useEffect, useState} from 'react';
import {Box, Text, render, useApp, useInput, useStdin, useStdout} from 'ink';
import {execFile} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';

const h = React.createElement;
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const pythonBackend = path.join(scriptDir, 'codex_hud.py');
const CARD_WIDTH = 38;
const WIDE_COLUMNS = 82;
const CARD_HORIZONTAL_CHROME = 6;
const MIN_BAR_WIDTH = 10;

function parseArgs(argv) {
	const args = {
		interval: 1,
		inkOnce: false,
		backendArgs: []
	};

	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		if (arg === '--interval' && argv[index + 1]) {
			args.interval = Number.parseFloat(argv[index + 1]) || 1;
			index += 1;
			continue;
		}
		if (arg.startsWith('--interval=')) {
			args.interval = Number.parseFloat(arg.slice('--interval='.length)) || 1;
			continue;
		}
		if (arg === '--ink-once') {
			args.inkOnce = true;
			continue;
		}
		args.backendArgs.push(arg);
		if ((arg === '--codex-home' || arg === '--session') && argv[index + 1]) {
			args.backendArgs.push(argv[index + 1]);
			index += 1;
		}
	}

	return args;
}

function loadSnapshot(backendArgs) {
	return new Promise((resolve, reject) => {
		execFile(
			'python3',
			[pythonBackend, '--once', '--json', ...backendArgs],
			{timeout: 5000, maxBuffer: 1024 * 1024},
			(error, stdout, stderr) => {
				if (error) {
					reject(new Error((stderr || error.message).trim()));
					return;
				}
				try {
					resolve(JSON.parse(stdout));
				} catch (parseError) {
					reject(parseError);
				}
			}
		);
	});
}

function percent(value, digits = 0) {
	if (value === null || value === undefined) {
		return '?';
	}
	return `${Number(value).toFixed(digits)}%`;
}

function resetText(timestamp, includeDate) {
	if (!timestamp) {
		return '-';
	}
	const value = new Date(timestamp * 1000);
	const hour = String(value.getHours()).padStart(2, '0');
	const minute = String(value.getMinutes()).padStart(2, '0');
	if (!includeDate) {
		return `${hour}:${minute}`;
	}
	return `${value.getFullYear()}年${value.getMonth() + 1}月${value.getDate()}日 ${hour}:${minute}`;
}

function isNotToday(timestamp) {
	if (!timestamp) {
		return false;
	}
	const value = new Date(timestamp * 1000);
	const now = new Date();
	return value.toDateString() !== now.toDateString();
}

function bar(remaining, width = 20) {
	if (remaining === null || remaining === undefined) {
		return '-'.repeat(width);
	}
	const filled = Math.max(0, Math.min(width, Math.round(width * remaining / 100)));
	return '■'.repeat(filled) + '·'.repeat(width - filled);
}

function remainingColor(remaining) {
	if (remaining === null || remaining === undefined) {
		return 'yellow';
	}
	if (remaining >= 30) {
		return 'green';
	}
	if (remaining >= 10) {
		return 'yellow';
	}
	return 'red';
}

export function cardLayoutForColumns(columns = 80) {
	const safeColumns = Math.max(1, columns || 80);
	const wide = safeColumns >= WIDE_COLUMNS;
	return {
		wide,
		cardWidth: wide ? CARD_WIDTH : safeColumns
	};
}

export function progressBarWidthForCard(cardWidth) {
	return Math.max(MIN_BAR_WIDTH, cardWidth - CARD_HORIZONTAL_CHROME);
}

function hasUsage(window) {
	return window?.used_percent !== null && window?.used_percent !== undefined;
}

function reachedWindow(snapshot, windowName) {
	const text = [snapshot?.limit_reached, snapshot?.limit_id]
		.filter(value => typeof value === 'string')
		.join(' ')
		.toLowerCase();
	if (!text) {
		return false;
	}
	if (windowName === 'primary') {
		return text.includes('primary') || text.includes('5h') || text.includes('5 hour') || text.includes('5-hour') || text.includes('window');
	}
	return text.includes('secondary') || text.includes('weekly') || text.includes('week') || text.includes('7d');
}

function exhaustedWindow(window) {
	return {
		...(window || {}),
		used_percent: 100,
		remaining_percent: 0
	};
}

function mergeWindow(nextWindow, previousWindow, exhausted) {
	const merged = hasUsage(nextWindow) ? nextWindow : {...(previousWindow || {}), ...(nextWindow || {})};
	return exhausted ? exhaustedWindow(merged) : merged;
}

function mergeSnapshot(next, previous) {
	if (!previous) {
		return {
			...next,
			primary: reachedWindow(next, 'primary') ? exhaustedWindow(next.primary) : next.primary,
			secondary: reachedWindow(next, 'secondary') ? exhaustedWindow(next.secondary) : next.secondary
		};
	}
	return {
		...next,
		primary: mergeWindow(next.primary, previous.primary, reachedWindow(next, 'primary')),
		secondary: mergeWindow(next.secondary, previous.secondary, reachedWindow(next, 'secondary'))
	};
}

function freshness(snapshot) {
	if (!snapshot?.source_updated_at) {
		return 'waiting for Codex limit telemetry';
	}
	if (snapshot.is_stale) {
		return `source stale ${snapshot.source_stale_seconds ?? '?'}s`;
	}
	return `source ${new Date(snapshot.source_updated_at).toLocaleString()}`;
}

function LimitCard({title, subtitle, window, weekly, width}) {
	const used = window?.used_percent;
	const remaining = window?.remaining_percent;
	const color = remainingColor(remaining);
	const reset = resetText(window?.resets_at, weekly || isNotToday(window?.resets_at));
	const barWidth = progressBarWidthForCard(width);

	return h(
		Box,
		{borderStyle: 'round', borderColor: color, paddingX: 1, width, flexDirection: 'column'},
		h(Text, {bold: true}, title),
		h(Text, {dimColor: true}, subtitle),
		h(Box, {height: 1}),
		h(Text, {color}, `已用：${percent(used)}`),
		h(Text, {color}, `剩余：${percent(remaining)}`),
		h(Text, {color}, bar(remaining, barWidth)),
		h(Text, null, `重置时间：${reset}`)
	);
}

function Hud({args}) {
	const {exit} = useApp();
	const {isRawModeSupported} = useStdin();
	const {stdout} = useStdout();
	const [snapshot, setSnapshot] = useState(null);
	const [error, setError] = useState(null);

	useInput((input, key) => {
		if (input === 'q' || key.escape) {
			exit();
		}
	}, {isActive: Boolean(isRawModeSupported)});

	const refresh = useCallback(async () => {
		try {
			const next = await loadSnapshot(args.backendArgs);
			setSnapshot(previous => mergeSnapshot(next, previous));
			setError(null);
			if (args.inkOnce) {
				setTimeout(exit, 50);
			}
		} catch (loadError) {
			setError(loadError.message);
			if (args.inkOnce) {
				setTimeout(exit, 50);
			}
		}
	}, [args.backendArgs, args.inkOnce, exit]);

	useEffect(() => {
		let cancelled = false;
		let running = false;
		const tick = async () => {
			if (running || cancelled) {
				return;
			}
			running = true;
			await refresh();
			running = false;
		};
		tick();
		const timer = setInterval(tick, Math.max(200, args.interval * 1000));
		return () => {
			cancelled = true;
			clearInterval(timer);
		};
	}, [args.interval, refresh]);

	const layout = cardLayoutForColumns(stdout?.columns || 80);
	const updated = snapshot ? new Date(snapshot.updated_at).toLocaleString() : '-';
	return h(
		Box,
		{flexDirection: 'column'},
		h(Text, {bold: true}, 'Codex HUD  Usage Remaining'),
		h(Text, {dimColor: !snapshot, color: error ? 'yellow' : undefined}, error || `updated ${updated} | ${freshness(snapshot)}`),
		h(Box, {height: 1}),
		snapshot
			? h(
				Box,
				{flexDirection: layout.wide ? 'row' : 'column', gap: 2},
				h(LimitCard, {title: '5 小时使用限额', subtitle: '滚动窗口', window: snapshot.primary, weekly: false, width: layout.cardWidth}),
				h(LimitCard, {title: '每周使用限额', subtitle: '订阅周期', window: snapshot.secondary, weekly: true, width: layout.cardWidth})
			)
			: h(Text, {color: 'yellow'}, 'loading usage snapshot...'),
		snapshot && h(Box, {height: 1}),
		snapshot && h(Text, {dimColor: true}, `Plan: ${snapshot.plan_type || '-'} | limit: ${snapshot.limit_id || '-'} | reached: ${snapshot.limit_reached || 'no'} | q to quit`)
	);
}

const args = parseArgs(process.argv.slice(2));
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	render(h(Hud, {args}), {exitOnCtrlC: true});
}
