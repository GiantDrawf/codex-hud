#!/usr/bin/env node

import React, {useCallback, useEffect, useState} from 'react';
import {Box, Text, render, useInput, useStdin, useStdout} from 'ink';
import {execFile} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';

const h = React.createElement;
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const pythonBackend = path.join(scriptDir, 'codex_hud.py');
const CARD_WIDTH = 38;
const CARD_GAP = 1;
const MIN_CARD_WIDTH = 24;
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

function setSubscriptionStart(backendArgs, date) {
	return new Promise((resolve, reject) => {
		execFile(
			'python3',
			[pythonBackend, 'subscription', ...codexHomeArgs(backendArgs), 'set-start', date],
			{timeout: 5000, maxBuffer: 1024 * 1024},
			(error, stdout, stderr) => {
				if (error) {
					reject(new Error((stderr || error.message).trim()));
					return;
				}
				resolve(stdout.trim());
			}
		);
	});
}

function codexHomeArgs(backendArgs) {
	const result = [];
	for (let index = 0; index < backendArgs.length; index += 1) {
		const arg = backendArgs[index];
		if (arg === '--codex-home' && backendArgs[index + 1]) {
			result.push(arg, backendArgs[index + 1]);
			index += 1;
			continue;
		}
		if (arg.startsWith('--codex-home=')) {
			result.push(arg);
		}
	}
	return result;
}

function percent(value, digits = 0) {
	if (value === null || value === undefined) {
		return '?';
	}
	return `${Number(value).toFixed(digits)}%`;
}

function tokenNumber(value) {
	if (value === null || value === undefined) {
		return '-';
	}
	return Number(value).toLocaleString();
}

function usd(value) {
	if (value === null || value === undefined) {
		return '-';
	}
	return `$${(Number(value || 0) / 1_000_000).toFixed(2)}`;
}

function displayWidth(value) {
	return Array.from(String(value)).reduce((width, char) => width + (char.charCodeAt(0) > 255 ? 2 : 1), 0);
}

function padDisplay(value, width) {
	const text = String(value);
	const padding = Math.max(0, width - displayWidth(text));
	return `${text}${' '.repeat(padding)}`;
}

function leftPad(value, width) {
	const text = String(value);
	const padding = Math.max(0, width - displayWidth(text));
	return `${' '.repeat(padding)}${text}`;
}

function fitDisplay(value, width, align = 'left') {
	const text = String(value);
	if (displayWidth(text) > width) {
		return truncateDisplay(text, width);
	}
	return align === 'right' ? leftPad(text, width) : padDisplay(text, width);
}

function truncateDisplay(value, width) {
	let used = 0;
	let result = '';
	for (const char of String(value)) {
		const charWidth = displayWidth(char);
		if (used + charWidth > width) {
			break;
		}
		result += char;
		used += charWidth;
	}
	return `${result}${' '.repeat(Math.max(0, width - used))}`;
}

function tokenSummaryRows(summary) {
	return [
		['今日', summary.today || {}],
		['昨日', summary.yesterday || {}],
		['本周限额', summary.current_weekly_limit || {}],
		['近 7 天', summary.last_7_days || {}],
		['当前账期', summary.current_subscription_period || {}]
	];
}

function subscriptionPeriodDisplay(period) {
	if (period?.display) {
		return period.display;
	}
	if (!period?.start || !period?.end) {
		return '-';
	}
	const start = new Date(`${period.start}T00:00:00`);
	const end = new Date(`${period.end}T00:00:00`);
	if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
		return '-';
	}
	end.setDate(end.getDate() - 1);
	return `${formatDate(start)}-${formatDate(end)}`;
}

function formatDate(value) {
	const month = String(value.getMonth() + 1).padStart(2, '0');
	const day = String(value.getDate()).padStart(2, '0');
	return `${value.getFullYear()}/${month}/${day}`;
}

function evenColumnWidths(totalWidth, count, gap) {
	const usableWidth = Math.max(count, totalWidth - gap * (count - 1));
	const base = Math.floor(usableWidth / count);
	const remainder = usableWidth % count;
	return Array.from({length: count}, (_, index) => base + (index < remainder ? 1 : 0));
}

function tokenSummaryHeader(widths) {
	return [
		fitDisplay('', widths[0]),
		fitDisplay('input', widths[1], 'right'),
		fitDisplay('output', widths[2], 'right'),
		fitDisplay('total', widths[3], 'right'),
		fitDisplay('cost', widths[4], 'right')
	].join('  ');
}

function tokenSummaryLine(label, usage, widths) {
	return [
		fitDisplay(label, widths[0]),
		fitDisplay(tokenNumber(usage.input_tokens), widths[1], 'right'),
		fitDisplay(tokenNumber(usage.output_tokens), widths[2], 'right'),
		fitDisplay(tokenNumber(usage.total_tokens), widths[3], 'right'),
		fitDisplay(usd(usage.estimated_cost_usd_micros), widths[4], 'right')
	].join('  ');
}

function resetText(timestamp, includeDate, compact = false) {
	if (!timestamp) {
		return '-';
	}
	const value = new Date(timestamp * 1000);
	const hour = String(value.getHours()).padStart(2, '0');
	const minute = String(value.getMinutes()).padStart(2, '0');
	if (!includeDate) {
		return `${hour}:${minute}`;
	}
	if (compact) {
		return `${value.getMonth() + 1}/${value.getDate()} ${hour}:${minute}`;
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

export function displayColorForMode(remaining, bossMode) {
	return bossMode ? undefined : remainingColor(remaining);
}

export function cardLayoutForColumns(columns = 80) {
	const safeColumns = Math.max(1, columns || 80);
	const availableCardWidth = Math.floor((safeColumns - CARD_GAP) / 2);
	return {
		cardWidth: Math.max(MIN_CARD_WIDTH, Math.min(CARD_WIDTH, availableCardWidth)),
		gap: CARD_GAP
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
	return `source ${shortTime(snapshot.source_updated_at)}`;
}

function shortTime(value) {
	return new Date(value).toLocaleTimeString();
}

function currentDateInputValue(snapshot) {
	const raw = snapshot?.subscription_period?.start || snapshot?.subscription_period?.configured_start;
	if (/^\d{4}-\d{2}-\d{2}$/.test(raw || '')) {
		return raw;
	}
	const now = new Date();
	const month = String(now.getMonth() + 1).padStart(2, '0');
	const day = String(now.getDate()).padStart(2, '0');
	return `${now.getFullYear()}-${month}-${day}`;
}

function titleLine(snapshot, updated, error) {
	const status = error || `updated ${updated} | ${freshness(snapshot)}`;
	return `Codex HUD | ${status}`;
}

function LimitCard({title, subtitle, window, weekly, width, bossMode}) {
	const used = window?.used_percent;
	const remaining = window?.remaining_percent;
	const color = displayColorForMode(remaining, bossMode);
	const reset = resetText(window?.resets_at, weekly || isNotToday(window?.resets_at), width < 34);
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

function TokenSummary({summary, width}) {
	if (!summary) {
		return null;
	}
	const rows = tokenSummaryRows(summary);
	const widths = evenColumnWidths(Math.max(50, width || 80), 5, 2);
	return h(
		Box,
		{flexDirection: 'column'},
		h(Text, {bold: true}, 'Token 汇总'),
		h(Text, {dimColor: true}, tokenSummaryHeader(widths)),
		...rows.map(([label, usage]) => h(Text, {key: label}, tokenSummaryLine(label, usage, widths)))
	);
}

function Hud({args}) {
	const {isRawModeSupported} = useStdin();
	const {stdout} = useStdout();
	const [snapshot, setSnapshot] = useState(null);
	const [error, setError] = useState(null);
	const [bossMode, setBossMode] = useState(false);
	const [periodInputActive, setPeriodInputActive] = useState(false);
	const [periodInput, setPeriodInput] = useState('');
	const [periodMessage, setPeriodMessage] = useState(null);

	useInput((input, key) => {
		if (periodInputActive) {
			if (key.return) {
				setPeriodInputActive(false);
				setSubscriptionStart(args.backendArgs, periodInput)
					.then(message => {
						setPeriodMessage(message || 'subscription period updated');
						return refresh();
					})
					.catch(saveError => setPeriodMessage(saveError.message));
				return;
			}
			if (key.escape) {
				setPeriodInputActive(false);
				setPeriodMessage(null);
				return;
			}
			if (key.backspace || key.delete) {
				setPeriodInput(value => value.slice(0, -1));
				return;
			}
			if (/^[0-9-]$/.test(input) && periodInput.length < 10) {
				setPeriodInput(value => `${value}${input}`);
			}
			return;
		}
		if (input === 'b' || input === 'B') {
			setBossMode(value => !value);
		}
		if (input === 'p' || input === 'P') {
			setPeriodInput(currentDateInputValue(snapshot));
			setPeriodInputActive(true);
			setPeriodMessage(null);
		}
	}, {isActive: Boolean(isRawModeSupported)});

	const refresh = useCallback(async () => {
		try {
			const next = await loadSnapshot(args.backendArgs);
			setSnapshot(previous => mergeSnapshot(next, previous));
			setError(null);
			if (args.inkOnce) {
				setTimeout(() => process.exit(0), 50);
			}
		} catch (loadError) {
			setError(loadError.message);
			if (args.inkOnce) {
				setTimeout(() => process.exit(0), 50);
			}
		}
	}, [args.backendArgs, args.inkOnce]);

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
	const updated = snapshot ? shortTime(snapshot.updated_at) : '-';
	return h(
		Box,
		{flexDirection: 'column'},
		h(Text, {bold: true, color: error && !bossMode ? 'yellow' : undefined}, titleLine(snapshot, updated, error)),
		h(Box, {height: 1}),
		snapshot
			? h(
				Box,
				{flexDirection: 'row', gap: layout.gap},
				h(LimitCard, {title: '5 小时使用限额', subtitle: '滚动窗口', window: snapshot.primary, weekly: false, width: layout.cardWidth, bossMode}),
				h(LimitCard, {title: '每周使用限额', subtitle: '订阅周期', window: snapshot.secondary, weekly: true, width: layout.cardWidth, bossMode})
			)
			: h(Text, {color: bossMode ? undefined : 'yellow'}, 'loading usage snapshot...'),
		snapshot?.token_summary && h(Box, {height: 1}),
		snapshot?.token_summary && h(TokenSummary, {summary: snapshot.token_summary, width: stdout?.columns || 80}),
		snapshot && h(Box, {height: 1}),
		snapshot && h(Text, {dimColor: true}, `period: ${subscriptionPeriodDisplay(snapshot.subscription_period)} | p 设置账期`),
		periodInputActive && h(Text, {color: bossMode ? undefined : 'cyan'}, `当前账期开始日 YYYY-MM-DD: ${periodInput}`),
		periodMessage && !periodInputActive && h(Text, {color: bossMode ? undefined : 'yellow'}, periodMessage)
	);
}

const args = parseArgs(process.argv.slice(2));
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	render(h(Hud, {args}), {exitOnCtrlC: true});
}
