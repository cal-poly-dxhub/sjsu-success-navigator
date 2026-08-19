# The facts

Every SJSU fact this app states lives in this directory, as CSV. Six files: a crawl list, the
places a student can be sent to, the buildings those places sit in, the contacts the app hands
out, and the campus shorthand it has to understand.

They are CSV so that you can open them in Excel, Numbers or Google Sheets, change one cell, and
save. You do not need to be able to read Python or TypeScript to correct a phone number.

## Why it is one directory

Until this directory existed, the SJSU Cares address was written out in `app/places.py` and
written out again in `frontend/src/lib/sjsuCares.ts`, and its mailbox was in `config.yaml` and
in that same TypeScript file. Nothing compared the copies. If the office had moved, the map card
and the "Talk to a person" panel would have disagreed inside one app, every test would still
have passed, and the first person to notice would have been a student standing at the wrong
door.

So there is now one row per fact, and both languages read it:

- **Python** reads these files directly, at import (`app/campus_data.py`). In a deployed Lambda
  they arrive as a layer that unpacks to `/opt`; in a checkout they are read from here.
- **The browser** never reads them. `frontend/scripts/generate-campus-data.mjs` compiles the
  rows it needs into a TypeScript module *during the frontend build*, so the site ships the
  values and makes no request for them. That generated file is gitignored and rewritten before
  every `npm run build` and `npm run dev`, so it cannot be committed and cannot go stale. If you
  edit it, your edit disappears at the next build - edit the CSV.

## The rules that apply to every file

**Every reader fails loudly.** A missing file, a missing column, an empty cell in a required
column, a duplicate key, a header with no rows underneath, or a row with the wrong number of
cells stops the process with the file name and the line number. Nothing here degrades to a shorter list. That is deliberate and it is
not defensive programming: a truncated `contacts.csv` that loaded quietly would drop a crisis
phone number off the panel a student in danger is shown, and a truncated `urls.csv` would make
the scraper's cleanup step delete the entire knowledge base. A build that fails is cheap. Either
of those is not.

**Keep it plain.** Save as CSV, UTF-8, with the header row intact. Do not rename or reorder
columns. Do not add a totals row, a blank separator row, or a note above the header. A cell that
contains a comma needs quotes around it, which every spreadsheet program does for you.

**No em dashes or en dashes** (`—`, `–`) in any cell a student reads. Use a plain hyphen. The
rest of the app strips them from generated text; nothing strips them from these files, and
`app/tests/test_places.py` will fail if one appears.

**Extra columns are fine.** Anything the code does not read is ignored, so a working note in a
spare column is safe.

**After you edit anything here:**

```
cd app     && python -m pytest -q     # the tables, the prompt, the panels
cd scraper && python -m pytest -q     # the crawl list
cd infra   && python -m pytest -q     # the deployed bundles (needs Docker running)
cd frontend && npm ci && npm run build # regenerates the TypeScript view
```

**Nothing you change here is live until somebody runs `cdk deploy`.** These files are baked into
the deployment: the Lambdas get them as a layer, the website gets them compiled into its
JavaScript. Editing a row and pushing it changes nothing that a student sees until the stack is
deployed again. If a phone number is wrong *right now*, say so to whoever runs deploys - do not
assume the commit fixed it.

---

## `urls.csv` - the pages the assistant knows about

**Edit it when** SJSU publishes a page the assistant should be able to cite, or takes one down.
This file *is* the knowledge base: the scraper fetches exactly these URLs every night, and
nothing else. A page not listed here cannot be quoted, linked, or found, however good it is.

**To add a row:** one line per page.

| column | what goes in it |
| --- | --- |
| `url` | the full address, starting `https://`. Must be unique in the file. |
| `section` | the grouping this page belongs to. Use a value already in the file unless you are genuinely opening a new area; it is read at retrieval time and drives how answers are ranked. |
| `title` | the page's own title, as SJSU wrote it. |
| `static_html` | `yes` if the content is in the page source, `pdf` for a PDF. Evidence, not input - see below. |
| `body_text_chars` | roughly how much text the page has. Evidence, not input. |

`static_html` and `body_text_chars` record *why* a page was judged scrapable. The scraper does
not read them. Fill them in anyway: the day a page stops working, they are what tells the next
person whether it ever did.

**Check a new page first.** Open it, press Ctrl-U (View Source), and search for a sentence you
can see on screen. If it is there, the page is static and `static_html` is `yes`. If it is not,
the text is drawn by JavaScript, the scraper will get an empty document, and the page does not
belong in this file. That is why `spartanrecreation.com`, `events.sjsu.edu` and `one.sjsu.edu`
are deliberately absent.

**Removing a row removes the page from the assistant's knowledge** at the next scrape, including
its document in the knowledge base. That is the intended way to retire a page.

**After editing:** `cd scraper && python -m pytest -q`. Verify the URLs against the live site by
opening them; a 404 in this file is a page the scraper reports as failed every night.

---

## `buildings.csv` - where a building is on the map

**Edit it when** a building in `places.csv` has no row here yet, or a coordinate is wrong.

| column | what goes in it |
| --- | --- |
| `key` | the short name `places.csv` refers to this building by, lowercase with hyphens. Must be unique. |
| `name` | what the building is called. |
| `lat`, `lon` | the exact point the map is drawn around, in decimal degrees. A dot for the decimal point, a leading minus for `lon`, no degree signs. |
| `note` | optional. Anything the next person should know before trusting the coordinate. |

**Adding a building takes one extra step,** and skipping it renders a broken image at a student.
The map on each location card is a picture rendered from these coordinates and committed to the
repository - the app draws no maps at run time and contacts no map service. After adding or
moving a building:

```
python -m pip install -r scripts/requirements.txt
python scripts/render_place_maps.py
```

That writes `frontend/public/places/<key>.webp`, which you then commit alongside the CSV.
`app/tests/test_places.py` fails if a building has no image, if an image has no building, or if
a coordinate lands outside campus.

**Verify against:** OpenStreetMap. Find the building, right-click the middle of it, "Show
address" or copy the coordinates. Then look at the rendered image and check the label under the
pin says the right building. Do not trust a search engine's pin: an earlier pass put the Student
Services Center in Evergreen, several miles away, and it looked perfectly plausible in a
spreadsheet.

---

## `places.csv` - the offices a student can be sent to

**Edit it when** an office moves, is renamed, or should become somewhere the assistant can point
at on a map.

| column | what goes in it |
| --- | --- |
| `key` | the short name the assistant writes to ask for this place, lowercase with hyphens. Must be unique. |
| `name` | the office's own name, printed at the top of the card. |
| `building` | **a `key` from `buildings.csv`.** This is the one link between the two files, and a typo costs the card its map. |
| `address` | the line under the name: how to find the door, without repeating the office's name. |
| `directions_destination` | what to search for in Google Maps when the student presses Directions. Use the **building's** name, not the coordinate: a student checking their route should see "Clark Hall", not six decimal places. |
| `when` | one short line saying what this place is for. The assistant is shown this list and picks a key from it, so this line is what decides whether it picks the right one. Write it the way a student would describe their problem. |
| `ground_truth_ids` | optional. Which entries in `eval/ground-truth.yaml` this address was checked against. |
| `note` | optional. Anything surprising - two rows here needed a person to settle. |

**Sixteen offices sit in five buildings**, and that is why there are two files: the address and
the room number belong to the office, the map belongs to the building. Five offices share the
Student Services Center's map and each has its own address line.

**A key that is not in this file produces no card at all** - not a guessed one. So adding an
office here is what makes the assistant able to point at it, and removing one makes it fall back
to stating the address in ordinary text.

**Verify against:** the office's own page on `sjsu.edu`, and `eval/ground-truth.yaml`, which is
this repository's record of facts checked against the live site. Never against the assistant's
own answers.

**After editing:** `cd app && python -m pytest -q`.

---

## `contacts.csv` - the numbers, mailboxes and links the app hands out

**Edit it when** a phone number, mailbox or link changes. This is the file where a mistake
matters most: some of these rows are what a student in a crisis is shown.

Every row has a `kind`, and the kind decides who reads it:

| `kind` | who reads it | what it is |
| --- | --- | --- |
| `safety` | the server only | one button on the crisis panel. Deliberately never sent to the browser as data - the panel is assembled server-side so there is exactly one copy of every crisis number. |
| `cares` | the website | the SJSU Cares panel: its phone number and its links. |
| `escalation` | the server, and the website | where a student's "please have someone contact me" message is addressed. |

| column | what goes in it |
| --- | --- |
| `kind` | one of `safety`, `cares`, `escalation`. |
| `id` | the short name the code refers to this row by. **Must be unique across the whole file.** Renaming one is a code change, not a data change - see below. |
| `label` | the words on the button or the heading. Required on a `safety` row. |
| `detail` | the line under the label; on the `escalation` row it is the email address itself. Required on a `safety` row. |
| `href` | where it goes: `tel:4089245678`, `https://...`. Required on a `safety` row. |
| `when` | `safety` rows only: one line saying when this resource fits. The assistant reads this list and picks from it, so this is what routes a student to the survivor advocate rather than a generic crisis line. Leave it empty to keep a row usable in the default panel without offering it as a choice. |
| `in_default_panel` | `yes` or `no`. `yes` puts the row on the panel a student sees when an emergency is recognised but no specific resource fits. The panel's order is this file's order. Anything other than yes or no is an error, because a typo silently meaning "no" is a missing number. |
| `note` | optional. |

**The mailbox has one row, not two.** `sjsu-cares` under `kind: escalation` is both the address
the escalation draft is sent to and the address behind the SJSU Cares panel's "Email us" link,
because it is one mailbox. `config.yaml` names it by id (`escalation.contact`); blanking that
key in `config.yaml` turns the whole escalate-to-a-person feature off.

**Ids are referred to by name from code**, so renaming one breaks something silently-looking:
`config.yaml` names the escalation row, `frontend/src/lib/sjsuCares.ts` names the cares rows,
and the assistant is taught the `safety` ids by name. If you rename an id, the build fails and
tells you where - which is the intended outcome. Adding and removing rows is safe; renaming is
a two-file change.

**Verify against:** the office's own page on `sjsu.edu`, and `eval/ground-truth.yaml`. Ring the
number. The sponsor's own reference sheets have been wrong about the CAPS phone number and the
SJSU Cares location before, which is why every one of these was checked against a live page.

**After editing:** `cd app && python -m pytest -q`.

---

## `abbreviations.csv` - the shorthand students actually type

**Edit it when** a building code or a programme's initials come into use, or one falls out of
use. Students write "SSC" and "AEC" rather than the full names, and the assistant is handed this
list so it can search for the real name. An abbreviation not on this list makes it ask what the
student meant, which is right but costs them a turn.

| column | what goes in it |
| --- | --- |
| `abbreviation` | what a student types. |
| `expansion` | the full official name. |

Both are required. Keep it a flat list of names, in the same rough alphabetical order the file is
in - it is read as a glossary, not as prose, so there is nowhere for an explanation to go.
"Tower Card" is the one entry whose expansion is a description rather than a name, because the
thing has no longer name.

**Verify against:** the campus map and the office's own page. This is the one file where being
approximately right is acceptable: a wrong expansion sends the assistant looking for the wrong
office, but it cannot put a wrong number in front of a student.

**After editing:** `cd app && python -m pytest -q`.

---

## If something goes wrong

A failing build names the file, the line and the column. The commonest causes:

- **"`x` is empty, and every row needs one"** - a required cell was left blank, often by
  inserting a row and only filling in part of it.
- **"is missing required column(s)"** - a column was renamed or deleted, or the file was saved
  in a format that dropped the header.
- **"is listed more than once"** - two rows share a key. One of them was a copy that never got
  edited.
- **"more cells than the header"** - a value contains a comma and is not wrapped in quotes, so
  everything after it shifted one column left. Most often a decimal point saved as a comma.
- **"fewer cells than the header"** - the file is cut off part-way through a row, which is what
  a half-finished save or a bad merge leaves behind.
- **"has a valid header and no rows"** - the file was saved with only its header, usually by
  saving the wrong sheet of a workbook.
- **"not found. Looked in: /opt/..., .../data/..."** - a file was renamed or moved out of this
  directory.

None of these can reach a deployed system: they stop the tests, and the tests run on every pull
request.
