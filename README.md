# Agent lead lists

Builds a list of real estate agents (name, email, phone, brokerage, listings
and sales volume) from every active listing in the markets you pick. Or it
takes a list you already have and verifies the phones, finds missing emails
and checks each agent's sales volume.

It's built to be run by Claude. You open the folder, paste your Monid key
into the page it opens, and say "build me a list". Claude asks which markets and add-ons you
want, shows you the price, and runs it once you say yes.

## Getting started

**1. Install it (once).** Paste one line, press Enter, wait about a minute.

- **Mac:** open Terminal (press Cmd+Space, type Terminal, press Enter) and paste:

  ```
  curl -fsSL https://raw.githubusercontent.com/topkdev/lead-builder/main/install.sh | bash
  ```

- **Windows:** open PowerShell (Start menu, type PowerShell, press Enter) and paste:

  ```
  irm https://raw.githubusercontent.com/topkdev/lead-builder/main/install.ps1 | iex
  ```

It installs its own copy of Python (you don't need one), puts everything in
a **Lead Builder** folder in your home folder, adds a **Lead Builder** icon
to your Desktop, and opens the app.

**2. Open it any time:** double-click **Lead Builder** on your Desktop. Your
browser opens the app. Keep the small window that appears open while you use
it; closing it quits the app.

**3. Update:** paste the same line again. Your key and your lists are kept.

To remove it, delete the Lead Builder folder and the Desktop icon.

### What you'll see

1. **Start a new list:** build one from every active listing in your area,
   or drop in a CSV or Excel file you already have.
2. **Tick what to do:** check phone numbers · find top producers (homes.com) ·
   find & verify emails. Each shows its price per agent.
3. **Your key:** the first time, the app shows a card with a link to sign up
   at Monid, a box to paste the key into, and a green check when it works.
4. **The price:** a table of every step, its cost and time. Approve it, or try
   25 agents first.
5. **Progress and results:** a live progress bar and spend meter, download
   buttons, and a button that opens the folder with your spreadsheets.

When the phone check finds numbers that aren't the agent's own cell (office
lines, landlines, missing numbers), it tells you how many and what finding
the real ones costs, and asks before spending anything.

### Prefer to talk it through?

Open the folder in [Claude Code](https://claude.com/claude-code) and type
**start**. Claude asks the same questions with clickable options and runs
everything for you. Same engine, same files.

## The one key

Everything runs on one key: [Monid](https://monid.ai), one prepaid balance
for every tool the builder uses. Sign up, add credit, create an API key and
paste it into the page the app opens. No other accounts are needed.

Twilio is a recommended extra on the same page: about $1.20 per 1,000 agents, from your own
Twilio balance, for a carrier's answer on numbers no record knows.

| What Monid does | Tool behind it | Price |
|---|---|---|
| New list: count listings, then get them with each agent's name, phone and email | Zillow and Realtor.com search (AnyAPI) | ~$2.50 per 1,000 listings |
| Phone checks: whose number it is, cell or landline | skip trace (AnyAPI) | ~$0.008/number, free when nobody is found |
| Finding the real cell for a wrong number | skip trace by email or name, the agent's Zillow profile, web search, a people database | ~$0.04 per wrong number |
| Email checks | AnyAPI email verify | ~$0.0066/email |
| Finding missing emails | Google search (MrScraper) and the agent's own sites | ~$0.0024/agent |
| Top producers | homes.com profile, then the agent's Zillow profile for the ones it doesn't have | ~$0.01/agent |

## What it costs

Every job is quoted before anything is spent, and it stops itself if spend
passes the approved budget by 25%.

**Checking a list you already have** (phones + homes.com volume):

| Agents | Cost |
|---|---|
| 500 | ~$5.50 |
| 1,000 | ~$11 |
| 2,000 | ~$22 |
| 4,000 | ~$43 |

Finding the real cell for the numbers that turn out to be wrong is a second,
separately approved step: about $0.04 per wrong number. If a quarter are
wrong, that's about $5 more per 1,000 agents.

**Building from scratch:** the listings themselves cost about $2.50 per
1,000, and about 9 in 10 agents come with an email, so there's little left
to search for. One ZIP code (about 200 listings) is about $0.50. A whole
county with every check switched on is roughly $100-200, mostly the phone
and email checks; limiting phone checks to agents with an email, or to
producers, is the biggest saving. The quote shows the exact figure.

## The files you get (`exports/<job>/`)

Each file name starts with the job name and ends with the date.

- `everyone.csv`: every agent and everything found about them
- `email-ready.csv`: verified, deliverable emails only, ready for Instantly or Smartlead
- `cell-phones.csv`: confirmed cell phones, on the agent's own record or their own Zillow profile, including the real cells found to replace office lines
- `cell-phones-probably.csv`: only with the optional Twilio key: a carrier says it's a cell, but nothing shows whose
- `producers.csv`: agents who sold 5+ listings in the last year, with flat-fee "listing mills" removed

## How it works

```
 Areas ─▶ 1. Listings ─▶ 2. Agents ─▶ 3. Emails ─▶ 4. Deep emails ─▶ 5. Verify emails ─▶ 6. Phones ─▶ 7. Volume ─▶ 8. Fix phones ─▶ CSVs
          (count on Zillow, (merge       (only the    (real Chrome,      (AnyAPI)             (skip trace) (homes.com    (find their
           buy on Realtor)   duplicates)  ones with    home connection)                                     via Monid)    own cell)
                                          no email)
 Your list ─▶ import, merge duplicates ────────────────┘ (from step 3 on, whichever add-ons you picked)

 Steps are numbered by what they do. They run in the order 1, 2, 3, 5, 7, 6, 8,
 then 4 (the slowest) and a last pass of 5, 6 and 8 over what 4 found.
```

1. **Listings.** Realtor.com's search (through Monid) gives each listing with
   its agent's name, phone, email and brokerage, but at most 25 per search,
   with no total and no paging. So each area is cut into price bands small
   enough to come back whole. The cutting is done on Zillow's search, which
   costs a flat $0.0008 a call, until each band holds under 20 listings; that
   count is what the quote is priced on. Then each band is bought once from
   Realtor.com. A band that still comes back full is halved and bought again,
   so nothing is cut off. Pending listings are dropped.
2. **Agents.** Listings are merged into agents on first + last name, with licence
   suffixes dropped and nicknames collapsed: "Chris Maners PA" and
   "Christopher Maners" are one person. The phone kept is the one on most of
   their listings, because a listing phone is often the office front desk.
   The email on their listings counts as found (step 5 still checks it).
3. **Emails.** Two Google searches per agent, then their brokerage and personal
   sites are fetched (contact, about and team pages included). An email is
   only accepted if it's printed on the page and matches the agent's name.
   Nothing is guessed. Zillow, Realtor.com and Homes.com show contact forms,
   not emails, so they are fetched last.
4. **Deep emails.** Sites behind Cloudflare-style challenges are retried in a
   real Chrome window from your home connection. Proxy IPs are flagged by
   those firewalls and home IPs aren't, so this step can't run on a server.
5. **Verify emails.** AnyAPI through Monid. Only "ok"
   goes in `email-ready.csv`. When the mail server won't say
   (`smtp_unreachable`), the verdict is "unknown", not "invalid".
6. **Phones.** First, free checks: a number that can't exist is dropped, and
   a number on two or more agents is marked as an office line. The rest go to
   a reverse skip trace through Monid (AnyAPI): whose record the number is on,
   and whether that record lists it as Wireless, a Landline or VoIP. A number
   the trace doesn't know is marked "not confirmed" (and costs nothing); step
   8 looks the agent up by email or name. Numbers flagged as known TCPA litigators (by tools that
   report it) are kept out of `cell-phones.csv`. You can limit phone checks to agents with an email, or to
   producers, which is where most of the savings are.
7. **Volume.** A search finds the agent's homes.com profile(s), and Firecrawl
   (via Monid) renders them. The parser reads seller deals in the last 12
   months and checks itself: the five-year seller and buyer totals must add
   up to the page's own "Closed Sales" figure, or the reading is flagged.
   The number printed on the profile is saved for step 8.
8. **Fix phones.** A number counts as wrong if it's shared with other agents,
   a landline, VoIP or toll-free, not confirmed by the trace, or on another
   person's record. First the agent's own skip trace record: the
   one step 6 fetched, when the number was theirs (free), or a trace by their
   email, then by name and city (about $0.008 each, only when someone is
   found). By name the trace often answers with a namesake in another state,
   so a record only counts when both names match and the person lives, or
   lived, in the agent's state. A Wireless number on their record becomes
   their cell, or confirms the one on file. Failing that, the tool collects
   candidates: the other numbers on the agent's own listings, their homes.com
   profile, the page their email was found on and two searches
   ("<name> realtor <city> cell").
   On a page, a number labelled cell, mobile or direct, or printed near their
   name, ranks first, and one labelled office or fax ranks last. Numbers
   already on anyone else in the job are excluded, and so are toll-free ones.
   Up to three candidates are traced. The first that is a cell and isn't on
   someone else's record replaces the old
   number everywhere in the exports. The old number stays in `office_or_old_phone`, and
   `phone_found_on` says where the new one came from.

Every step saves as it goes and only picks up unfinished rows. Stopping and
re-running is always safe and never pays twice. A run stops outright on
anything that would otherwise fail every remaining row quietly: an empty
balance, a rejected key, or the budget.

### What the list is, and isn't

It is every agent with at least one *active* listing on Realtor.com in those
areas, the week you run it. It is weighted toward producers. It is not
every licensed agent: buyer's agents and agents with no current listing
don't appear. Re-run monthly to catch new listers.

On a test ZIP, 184 of 189 agents came with an email on their listings.

## By hand

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python leads.py doctor
.venv/bin/python leads.py find-market "Hillsborough County, FL"
.venv/bin/python leads.py new tampa --build --market "Hillsborough County, FL" --min-listings 2 --addons all
.venv/bin/python leads.py quote tampa
.venv/bin/python leads.py approve tampa --budget 120
.venv/bin/python leads.py run tampa
```

`leads.py --help` lists every option. Tests: `.venv/bin/python -m unittest discover -s tests -t .`

## Responsible use

- Cold email must follow CAN-SPAM: a real sender and address, and a working opt-out.
- Mass texting or auto-dialing cell numbers is covered by the TCPA and the
  Do Not Call registry. Get consent or legal advice first.
- New lists come from Realtor.com listing data through Monid, to build your
  own prospect list. Don't republish the data.
