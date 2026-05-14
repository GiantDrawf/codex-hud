#!/usr/bin/env node

import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs';
import path from 'node:path';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const watchedFiles = [
	path.join(scriptDir, 'ink_hud.mjs'),
	path.join(scriptDir, 'codex_hud.py')
];

let child = null;
let restartTimer = null;
let restarting = false;

function clearScreen() {
	if (process.stdout.isTTY) {
		process.stdout.write('\x1b[2J\x1b[3J\x1b[H');
	}
}

function start() {
	clearScreen();
	child = spawn(process.execPath, [path.join(scriptDir, 'ink_hud.mjs'), ...process.argv.slice(2)], {
		stdio: 'inherit'
	});
	child.on('exit', () => {
		child = null;
		if (!restarting) {
			process.exit(0);
		}
	});
}

function restart() {
	clearTimeout(restartTimer);
	restartTimer = setTimeout(() => {
		if (!child) {
			start();
			return;
		}
		const previous = child;
		restarting = true;
		previous.once('exit', () => {
			restarting = false;
			start();
		});
		previous.kill('SIGTERM');
	}, 100);
}

for (const file of watchedFiles) {
	fs.watch(file, {persistent: true}, restart);
}

for (const signal of ['SIGINT', 'SIGTERM']) {
	process.on(signal, () => {
		if (child) {
			child.kill(signal);
		}
		process.exit(0);
	});
}

start();
