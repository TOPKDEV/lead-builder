# Lead list builder: how to run this for the person in front of you

The person using this repo is a real estate media pro, not a programmer. They
want a list of real estate agents with emails, phone numbers and sales
volume, and they expect you to do every technical step. Your job: show them
checkboxes, ask the few follow-ups that matter, quote the price, get a clear
yes, run it, and hand them the files.

Talk plainly. No jargon, no stack traces pasted at them, no homework. You
run every command. Use the **AskUserQuestion tool** for every choice: it
shows clickable options right in the terminal (multiSelect gives checkboxes),
and it always adds a free-text "Other". Ask in plain chat only for things
that can't be options: market names, a file location.

All commands run from the repo root with the virtualenv's Python:
`.venv/bin/python leads.py ...` (on Windows: `.venv\Scripts\python leads.py ...`).

---

## 1. When they say "start" (or anything at all, first time)

Quietly, without narrating:
1. `python3 --version` must be 3.9+. If not, help them install it (Mac: `xcode-select --install`; Windows: `winget install Python.Python.3.12`).
2. If `.venv/` is missing: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
3. `.venv/bin/python leads.py jobs`. If they already have a job in progress,
   ask whether to pick it up (`status`) or start a new one.

Then greet them in one line and go straight to the menu.

If they'd rather click than chat, there's an app with the same steps: run
`.venv/bin/python leads.py app` in the background (it opens their browser),
or tell them to double-click **Start Lead Builder.command** (Mac) or
**Start Lead Builder.bat** (Windows). Jobs made in either place show up in
both.

## 2. The menu: one AskUserQuestion call with two questions

**Question 1**, header "Your list", single choice:
- "Build a new list": every agent with an active listing in your area, with their email (from Realtor.com)
- "Use a list I already have": a CSV or Excel file of agents

**Question 2**, header "What to do", multiSelect:
- "Check phone numbers": is each one a cell, or an office line or landline? (If some are wrong, I'll tell you how many and quote finding the real ones.)
- "Find top producers": listings each agent sold last year, from homes.com
- "Find & verify emails": find missing emails and check they won't bounce

What each checkbox turns on (`--addons`):

| Checkbox | Add-ons |
|---|---|
| Check phone numbers | `verify_phones` (finding the real cells is a second step, after the check; see section 6) |
| Find top producers | `volume` |
| Find & verify emails | `emails,verify_emails` (on their own list, `emails` only searches rows with no email) |

If they pick nothing in question 2 and they're building a list, the list
still comes with names, phones, brokerages and listing stats; say so and
confirm that's what they want.

## 3. Follow-ups, only the ones their choices need

Batch these into one AskUserQuestion call where you can (up to 4 questions).

**Building a new list**
- In chat: "Which areas? Counties work best, cities and ZIP codes too."
  For each, run `leads.py find-market "<what they said>"` (free: a built-in
  catalog of US counties, cities and ZIP codes, with populations). Pick the
  match and confirm it by name ("Hillsborough County, FL, 1.46 million
  people, right?").
- Header "Which agents", single: "Everyone with an active listing" /
  "2+ active listings (about 40%, the active ones)" / "5+ active listings
  (about 8%, the heavy hitters)". That's `--min-listings 1|2|5`.

**Their own list**
- In chat: "Where's the file? Easiest: drag it from Finder or Explorer into
  this window, and its location gets pasted in." (Or they can put it in this
  folder and tell you its name.)

**If phones are checked** (header "Phone checks"):
- Single, "Which agents get phone checks?": "Everyone" / "Only agents with
  an email" / "Only top producers" (only offer this one if they picked Find
  top producers). That's `--phones-for all|emails|producers`. Recommend the
  narrowest one that fits how they'll use the list; it's the biggest saving.

**If emails are chosen** (header "Emails"):
- Single: "Standard search (fast)" / "Also try the slow Chrome pass (finds
  about 30% more of the misses; takes hours and this computer has to stay
  on)". The second adds `deep_emails`.

**If producers are chosen and it matters**: 5 listings sold a year is the
default bar (`--producer-min`). Only ask if they mention a different bar.

Then name the job yourself (short, dated, e.g. `tampa-oct`) and create it:

```
# building
.venv/bin/python leads.py new tampa-oct --build --market "Hillsborough County, FL" --min-listings 2 \
    --addons verify_phones,fix_phones,volume --phones-for producers
# their list
.venv/bin/python leads.py new my-list --enrich "/Users/them/Downloads/agents.csv" --addons verify_phones,volume
```

On their own list, `new` prints which column it read as name, email, phone,
brokerage and city. Say it back in plain words ("I'm reading 'Agent Name'
as the name and 'Cell' as the phone"). If something's wrong, re-run `new`
with `--replace --map field=Column`. Tell them how many agents it found and
how many duplicate rows it merged.

## 4. The key: one Monid key, pasted in a page in their browser

Everything runs on one key: Monid (monid.ai), one prepaid balance for every
tool the builder uses. Run `leads.py doctor` first; if the Monid key is
missing or rejected:

```
.venv/bin/python leads.py keys --job tampa-oct
```

Run it in the background. It opens a page in their browser: what Monid is
for, a link to sign up, a box to paste the key, and a green check once it
works. Tell them: "A page just opened in your browser. Sign up at Monid, add
some credit, create an API key, paste it in and click Save & test." (If no
window appeared, give them the link it printed.) When the command finishes,
it prints whether the key works.

If they'd rather paste a key into this chat, save it for them without
repeating it back: `.venv/bin/python leads.py set-key MONID_API_KEY <key>`.

Twilio is an optional second key, and nothing needs it. With it, numbers no
record knows and no search could replace get a carrier's answer (about $1.20
per 1,000 agents, from their own Twilio balance), and the cells among them go
in their own "probably" file. It's on the same keys page. Only mention it if
they ask how to get more cells, or the phone results disappoint them.

| What Monid does | Tool behind it | Price |
|---|---|---|
| New list: count listings, then get them with each agent's name, phone, email | Zillow and Realtor.com search (AnyAPI) | ~$2.50 per 1,000 listings |
| Phone checks: whose number, cell or landline | skip trace (AnyAPI) | ~$0.008/number, free when nobody is found |
| Finding real cells | skip trace by email or name, the agent's Zillow profile, web search, a people database | ~$0.04 per wrong number |
| Email checks | AnyAPI email verify | ~$0.0066/email |
| Finding missing emails, top producers | Google search, homes.com via Firecrawl | ~$0.0024/agent, ~$0.003/agent |

## 5. The price, and their yes

```
.venv/bin/python leads.py quote tampa-oct
```

For a new list, the quote first counts the active listings in their areas
(a few cents on Zillow; about a minute per ZIP, longer for a county), so the
price is based on real numbers. The agent count is still an estimate until
the listings are in: in a single ZIP there can be twice as many agents as
it guesses, since most have only one listing there. Show it as a
short table: step, how many, cost, time, total. Say which numbers are
estimates; the quote prints notes for those.

Ballparks to sanity-check against: checking phones and volume on a list of
1,000 agents is about $11. One ZIP code (about 200 listings) is about $0.50
for the list itself. A whole county from scratch with everything switched on
is roughly $100-200, and the phone and email checks are most of it.

Then one AskUserQuestion, header "Go ahead?":
- "Yes, approve up to $Z" (Z is the budget the quote suggests)
- "Try 25 agents first" (a few cents to a dollar; show them 5 to 10 rows, then ask again)
- "Make it cheaper"

"Make it cheaper": offer, in this order: phone checks for fewer agents
(`--phones-for`), fewer markets or a higher `--min-listings`,
dropping the Chrome pass. (Finding real cells is never in the first quote;
it's priced separately once the check shows how many are wrong.) Re-create the job with `--replace` and re-quote.

Only after a clear yes:

```
.venv/bin/python leads.py approve tampa-oct --budget Z
```

Never approve on your own, and never raise a budget without asking. A run
stops itself if spend passes the budget by 25%. A trial is
`leads.py run NAME --limit 25` after approving a small budget; the full run
later continues where it stopped, and nothing is paid for twice.

## 6. Running it

Runs take minutes for a small list and up to overnight for a few counties.
Start it in the background with a log, and keep the computer awake:

```
# Mac (caffeinate stops the Mac from sleeping)
caffeinate -i .venv/bin/python -u leads.py run tampa-oct > jobs/tampa-oct/run.log 2>&1
# Linux / Windows
.venv/bin/python -u leads.py run tampa-oct > jobs/tampa-oct/run.log 2>&1
```

Steps run in this order: count, listings, agents, emails (only agents whose
listings had none), verify emails, volume,
phones, fixing phones, then the slow Chrome email pass and a last round of
checks on what it found. The files in `exports/` are rewritten after every
step, so the list is usable before the slow pass finishes.

Check on it with `.venv/bin/python leads.py status tampa-oct` and the end of
the log, and give them short updates. If they close the laptop the run
stops; running the same command again picks up where it left off.

If the Chrome pass is on and Chrome automation isn't installed, the run
skips it and says how to add it:
`.venv/bin/pip install patchright && .venv/bin/patchright install chromium`.
It opens real Chrome windows; tell them that's expected.

### After the phone check: the second step

When a job checks phones, the run prints a summary as soon as the check
finishes, like:

```
312 of 1,000 numbers look wrong (not confirmed as theirs: 150, office lines shared by several agents: 90, landlines: 35, no number at all: 37).
Finding their real cells would cost about $13.40 (about $0.043 each; you only pay for the search, found or not).
```

(`leads.py status NAME` shows the same summary any time later.) Tell them in
plain words, then one AskUserQuestion, header "Real cells":
- "Yes, find the real numbers (~$X)"
- "No, keep the list as it is"

On yes:

```
.venv/bin/python leads.py add tampa-oct fix_phones
.venv/bin/python leads.py quote tampa-oct      # now priced on the real count
.venv/bin/python leads.py approve tampa-oct --budget <the budget the quote suggests>
.venv/bin/python -u leads.py run tampa-oct > jobs/tampa-oct/run.log 2>&1
```

The new budget includes what's already been spent; the quote suggests the
right number. The run only does the new work. Numbers it can't find a
replacement for stay as they were, with a note saying why they look wrong.
When it's done, tell them how many were replaced.

### If it says STOPPED
It's always something they can fix, and everything finished so far is
saved. Translate the message, help fix it, and run the same command again:
- out of credits or funds: top up that service;
- out of Monid credit (402): add credit at monid.ai;
- key rejected: `leads.py keys` and paste it again;
- past the budget: show `status`, ask whether to approve more;
- 30 failures in a row: run `leads.py doctor`; usually the internet dropped or a key expired;

## 7. Handing over the results

Files land in `exports/<job>/` (the run prints the paths):

| File | What's in it | Use it for |
|---|---|---|
| `*-everyone-*.csv` | every agent and everything found | their master copy |
| `*-email-ready-*.csv` | verified deliverable emails only | upload to Instantly, Smartlead, etc. |
| `*-email-ready-UNVERIFIED-*.csv` | emails not verified (no verify step) | verify before sending |
| `*-email-probably-ok-*.csv` | emails from a listing or their own list that no check can confirm or deny | a second batch, after the verified ones |
| `*-cell-phones-*.csv` | confirmed cells: on the agent's own record or their own Zillow profile, including the ones `fix_phones` found | calling and texting |
| `*-cell-phones-probably-*.csv` | only with Twilio: a carrier says it's a cell, but nothing shows whose | calling; expect some wrong people |
| `*-producers-*.csv` | sold N+ listings last year, mills removed | best prospects for paid offers |

Columns include `first_name` for mail merge, `active_listings`,
`median_list_price`, `listings_sold_last_12mo`, `phone_type`,
`carrier_says`, `phone_registered_to`, `phone_matches_agent`, `office_or_old_phone` and
`phone_note` (why a number was replaced, or why it looks wrong if no
replacement was found), and (for their own lists) every original column. Summarise the numbers for them: agents,
emails, verified, cells, producers.

Mention once, briefly, when handing over phone numbers and emails:
- Cold email must follow CAN-SPAM: real sender and address, a working opt-out.
- Texting or auto-dialing cell phones is covered by the TCPA and the Do Not
  Call registry. Dialing by hand is lower risk; mass texting or auto-dialing
  cells without consent is how people get sued. If they plan to do that,
  tell them to check with a lawyer first.

## 8. Rules for you

- Never commit or share `.env`, never print a key back to them, and never
  send keys anywhere except the provider they belong to.
- Don't change code to get around a provider problem (a block, a rate
  limit, a parser that stopped matching) without telling them what you're
  changing and why.
- A run is safe to stop and re-run. Prefer re-running to clever fixes.
- Every step is idempotent and saves as it goes. Deleting `jobs/<name>/`
  throws away everything paid for in that job, so ask first.
- New lists come from Realtor.com listing data through Monid. It's for
  building their own prospect list, not for republishing the listings.

## Reference

```
leads.py app                         the whole thing as a page in the browser
leads.py keys [--job NAME]           browser page to paste and test the keys
leads.py set-key NAME VALUE          save one key from the chat
leads.py doctor                      whether the key works, and what can run
leads.py find-market "Tampa, FL"     look up a county, city or ZIP code (free)
leads.py new NAME (--build --market "AREA, ST" ... | --enrich FILE [--map field=Col])
             [--addons all|none|a,b] [--min-listings N] [--min-price N] [--max-price N]
             [--types house,condo,townhouse,multifamily] [--producer-min N]
             [--phones-for all|emails|producers] [--replace]
leads.py quote NAME                  count, price, time
leads.py approve NAME --budget N     record their yes
leads.py add NAME fix_phones         add a step to a job (then quote and approve again)
leads.py run NAME [--limit N] [--only STEP]
leads.py status NAME
leads.py export NAME                 rewrite the CSVs
leads.py jobs
leads.py scout [--query "..."]       search Monid's catalog for useful tools, with live prices (free)
```

If they ask what else Monid can do, or for a cheaper or better tool for a
step, run `scout` (free) and summarise `exports/monid-catalog-<date>.md`.
To see exactly what one tool answers before relying on it, run it once:
`leads.py try PROVIDER ENDPOINT '<json>'` (add `--query` for GET tools; it
costs that tool's listed price) and read `exports/tool-samples/`.

How it works, for when something needs explaining: see README.md, "How it works".
