/**
 * Turn the repo-root `data/` CSVs into a TypeScript module the site imports.
 *
 * WHY A GENERATED MODULE AND NOT A FETCH. The facts are known when the site is built, and a
 * student reading an answer should not wait on a second request - or get a page that renders
 * without its address because that request failed. So the CSVs are read at BUILD time and the
 * values are compiled into the bundle, exactly as the hand-written constants they replace
 * were. Nothing about the runtime changes.
 *
 * WHY IT CANNOT GO STALE. The output is gitignored, so it cannot be committed, and npm's
 * `prebuild`/`predev` hooks run this before `astro` ever starts - so every build regenerates
 * it from the CSVs on disk. A hand-edit survives until the next build and no further, and the
 * file says so at the top of itself. This is the half of the job that makes `data/` the single
 * source rather than the first of two.
 *
 * WHAT IS DELIBERATELY LEFT OUT: the `safety` rows of contacts.csv. The crisis panel is
 * assembled by the SERVER, from that same table, and the label, number and link a student in
 * danger reads are table-authored on purpose (app/safety.py). Shipping those rows to the
 * browser would put a second copy in reach of the next person who needs a contact on screen,
 * and the whole point of that design is that there is one.
 *
 * Run by hand while developing: `node scripts/generate-campus-data.mjs`.
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..');
// `data/` sits at the repo root, one level above frontend/. The Docker build that runs at
// `cdk synth` copies it to the same relative place (see _astro_bundling in
// infra/infra/infra_stack.py), so this one path is correct in a checkout, in CI and in the
// container.
const DATA_DIR = process.env.CAMPUS_DATA_DIR ?? resolve(FRONTEND, '..', 'data');
const OUT_FILE = join(FRONTEND, 'src', 'lib', 'generated', 'campusData.ts');

/** Fatal, and every failure in this file uses it: see the loud-or-nothing rule in data/README.md. */
class DataError extends Error {}

/**
 * Minimal RFC 4180 reader: quoted fields, embedded commas, doubled quotes, CRLF.
 *
 * Hand-rolled rather than a dependency because this runs before `npm ci` has any say in what
 * the build needs, and because the alternative is adding a package to the site's lockfile for
 * eleven lines of parsing.
 */
function parseCsv(text, file) {
	const rows = [];
	let row = [];
	let field = '';
	let quoted = false;
	for (let i = 0; i < text.length; i += 1) {
		const char = text[i];
		if (quoted) {
			if (char === '"') {
				if (text[i + 1] === '"') {
					field += '"';
					i += 1;
				} else {
					quoted = false;
				}
			} else {
				field += char;
			}
			continue;
		}
		if (char === '"') {
			quoted = true;
		} else if (char === ',') {
			row.push(field);
			field = '';
		} else if (char === '\n' || char === '\r') {
			if (char === '\r' && text[i + 1] === '\n') i += 1;
			row.push(field);
			rows.push(row);
			row = [];
			field = '';
		} else {
			field += char;
		}
	}
	if (quoted) throw new DataError(`${file}: a quoted field is never closed.`);
	if (field !== '' || row.length > 0) {
		row.push(field);
		rows.push(row);
	}
	return rows.filter((cells) => cells.some((cell) => cell.trim() !== ''));
}

/** One CSV as row objects, or a DataError naming the file and the line. Never a short list. */
function readTable(file, { required, optional = [] }) {
	const path = join(DATA_DIR, file);
	let text;
	try {
		text = readFileSync(path, 'utf8');
	} catch (cause) {
		throw new DataError(
			`${path} could not be read: ${cause.message}. The site's facts live in the repo-root ` +
				`data/ directory; set CAMPUS_DATA_DIR if it is somewhere else.`,
		);
	}
	const [header, ...body] = parseCsv(text, file);
	if (!header) throw new DataError(`${file} is empty.`);
	const columns = header.map((name) => name.trim());
	const missing = [...required, ...optional].filter((name) => !columns.includes(name));
	if (missing.length > 0) {
		throw new DataError(`${file} is missing required column(s): ${missing.join(', ')}.`);
	}
	const rows = body.map((cells, index) => {
		// The row's SHAPE, checked before any of its cells, and the same pair of checks
		// app/campus_data.py makes - both catch a corruption where every cell that is read
		// looks perfectly well formed. Too many cells is a stray comma inside a value, which
		// shifts everything after it one column left; too few is a file that was cut off.
		if (cells.length !== columns.length) {
			throw new DataError(
				`${file} line ${index + 2}: ${cells.length} cells against the header's ` +
					`${columns.length}. A stray comma inside a value shifts every cell after it (wrap ` +
					'the value in double quotes); a short row means the file was cut off.',
			);
		}
		const row = {};
		for (const name of [...required, ...optional]) {
			row[name] = (cells[columns.indexOf(name)] ?? '').trim();
		}
		for (const name of required) {
			if (!row[name]) {
				throw new DataError(
					`${file} line ${index + 2}: \`${name}\` is empty, and every row needs one.`,
				);
			}
		}
		return row;
	});
	if (rows.length === 0) {
		throw new DataError(
			`${file} has a valid header and no rows. An empty table is not a small table - it is a ` +
				'page that renders without the facts on it.',
		);
	}
	return rows;
}

/** Rows keyed by a column, with a duplicate key fatal rather than silently overwriting. */
function keyBy(rows, column, file) {
	const keyed = new Map();
	for (const row of rows) {
		if (keyed.has(row[column])) {
			throw new DataError(`${file}: \`${column}\` ${row[column]} is listed more than once.`);
		}
		keyed.set(row[column], row);
	}
	return keyed;
}

const places = keyBy(
	readTable('places.csv', {
		required: ['key', 'name', 'building', 'address', 'directions_destination', 'when'],
		optional: ['ground_truth_ids', 'note'],
	}),
	'key',
	'places.csv',
);

const contactRows = readTable('contacts.csv', {
	required: ['kind', 'id'],
	optional: ['label', 'detail', 'href', 'when', 'in_default_panel', 'note'],
});
const contacts = keyBy(contactRows, 'id', 'contacts.csv');

// The rows the browser is allowed to see. `safety` is absent on purpose - see the header.
const BROWSER_KINDS = new Set(['cares', 'escalation']);

/** A required cell, or a DataError that names the row a person has to go and fix. */
function cell(id, column) {
	const row = contacts.get(id);
	if (!row) {
		throw new DataError(
			`contacts.csv has no row with id ${id}, and the site reads it by that name. Either the ` +
				'row was renamed or it was deleted; see data/README.md.',
		);
	}
	if (!row[column]) {
		throw new DataError(`contacts.csv row ${id} has an empty \`${column}\`, and the site shows it.`);
	}
	return row[column];
}

function place(key) {
	const row = places.get(key);
	if (!row) {
		throw new DataError(
			`places.csv has no row with key ${key}, and the site reads it by that name. See data/README.md.`,
		);
	}
	return row;
}

// Read every value the site needs BEFORE writing anything, so a bad row leaves the previous
// generated file in place rather than a half-written one.
const caresLocation = place('sjsu-cares').address;
const browserContacts = contactRows.filter((row) => BROWSER_KINDS.has(row.kind));

// Every row the SJSU Cares modal reads, and the cell it reads out of it. Checked here so a
// renamed or emptied row fails the BUILD with the id in the message, rather than rendering a
// panel with a blank where the phone number goes. lib/sjsuCares.ts is the only consumer and
// this list mirrors it.
const REQUIRED_CARES_CELLS = {
	'sjsu-cares': 'detail',
	'sjsu-cares-phone': 'detail',
	'sjsu-cares-request-form': 'href',
	'sjsu-cares-contact-page': 'href',
	'sjsu-cares-services-index': 'href',
	'sjsu-cares-food': 'href',
	'sjsu-cares-housing': 'href',
	'sjsu-cares-financial': 'href',
	'sjsu-cares-parenting': 'href',
};
for (const [id, column] of Object.entries(REQUIRED_CARES_CELLS)) cell(id, column);

const lines = [
	'// GENERATED FILE - DO NOT EDIT, AND DO NOT COMMIT.',
	'//',
	'// Written by frontend/scripts/generate-campus-data.mjs from the CSVs in the repo-root',
	'// data/ directory, before every `npm run build` and `npm run dev`. An edit here survives',
	'// until the next build and no longer; the fact you want to change is a row in data/.',
	'',
	'export type CampusPlace = {',
	'\treadonly key: string;',
	'\treadonly name: string;',
	'\treadonly building: string;',
	'\treadonly address: string;',
	'\treadonly directionsDestination: string;',
	'\treadonly when: string;',
	'};',
	'',
	'/** data/places.csv, in file order. The same rows app/places.py resolves a `<place>` key against. */',
	'export const CAMPUS_PLACES = {',
	...[...places.values()].map(
		(row) =>
			`\t${JSON.stringify(row.key)}: {\n` +
			`\t\tkey: ${JSON.stringify(row.key)},\n` +
			`\t\tname: ${JSON.stringify(row.name)},\n` +
			`\t\tbuilding: ${JSON.stringify(row.building)},\n` +
			`\t\taddress: ${JSON.stringify(row.address)},\n` +
			`\t\tdirectionsDestination: ${JSON.stringify(row.directions_destination)},\n` +
			`\t\twhen: ${JSON.stringify(row.when)},\n` +
			'\t},',
	),
	'} as const satisfies Record<string, CampusPlace>;',
	'',
	'export type CampusContact = {',
	'\treadonly kind: string;',
	'\treadonly id: string;',
	'\treadonly label: string;',
	'\treadonly detail: string;',
	'\treadonly href: string;',
	'};',
	'',
	'/**',
	' * data/contacts.csv, in file order, WITHOUT its `safety` rows: the crisis panel is built',
	' * server-side from those, and one copy of a crisis number is the whole design.',
	' */',
	'export const CAMPUS_CONTACTS = {',
	...browserContacts.map(
		(row) =>
			`\t${JSON.stringify(row.id)}: {\n` +
			`\t\tkind: ${JSON.stringify(row.kind)},\n` +
			`\t\tid: ${JSON.stringify(row.id)},\n` +
			`\t\tlabel: ${JSON.stringify(row.label)},\n` +
			`\t\tdetail: ${JSON.stringify(row.detail)},\n` +
			`\t\thref: ${JSON.stringify(row.href)},\n` +
			'\t},',
	),
	'} as const satisfies Record<string, CampusContact>;',
	'',
	'/** places.csv `sjsu-cares`: the same address the location card prints, with no name on it. */',
	`export const SJSU_CARES_ADDRESS = ${JSON.stringify(caresLocation)};`,
	'',
];

mkdirSync(dirname(OUT_FILE), { recursive: true });
writeFileSync(OUT_FILE, lines.join('\n'), 'utf8');
console.log(
	`campus data: ${places.size} places, ${browserContacts.length} contacts -> ` +
		`${relative(FRONTEND, OUT_FILE)}`,
);
