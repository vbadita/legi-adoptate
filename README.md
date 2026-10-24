# legi-adoptate

Legile adoptate de Parlamentul României, pe zile, explicate pe înțelesul tuturor.
*(Laws adopted by the Romanian Parliament, by day, explained in plain language.)*

For each law: **3 status columns** that update as it moves on:

| 1. Adoptată de Parlament | 2. Promulgată | 3. Publicată în Monitorul Oficial |
|---|---|---|
| final vote, chamber, vote count | decree + law number | M.Of. number and date |

## Where the data comes from

Public pages of the Chamber of Deputies (`www.cdep.ro`, allowed by its `robots.txt`),
read politely: at most one request every 2 seconds, with a User-Agent naming this repo.

| Page | Gives |
|---|---|
| `upl_pck2015.proiect?idp=N` (legislative procedure) | dated timeline: votes in each chamber, deposit, promulgation |
| `upl_pck2015.lista?anl=YYYY` | bills that became laws in a year |
| `upl_pck2015.lista?std=G/Pp/C/R` | bills between the final vote and promulgation |
| `legis_acte_parlam2015` + `legis_pck.htp_act` | law number, date, Monitorul Oficial |

The Chamber's own disclaimer applies: texts on its site "nu au caracter oficial";
the official text is the one published in Monitorul Oficial.

### "Adopted by Parliament on day X"

Right after Parliament's **definitive** vote a law is deposited at the Secretary General
(for the constitutional-challenge window). So the definitive adoption is the **last vote
before each deposit**. A law can have several rounds (e.g. the President asks for a
reexamination); each round appears on its own day. A law voted today, not yet deposited,
is shown as provisional until the deposit confirms it.

## Data (`data/`, committed: the Git history is the audit log)

| Path | What |
|---|---|
| `data/laws/<idp>.json` | one law: adoption rounds with votes, promulgation, law number, Monitorul Oficial, full timeline |
| `data/mo/<YYYY-MM-DD>.json` | Partea I issues of that day, from Monitorul Oficial (cross-check) |

Files are rewritten only when their content changes, so every diff is a real status change.

### Monitorul Oficial

Partea I is free by law ("gratuit și liber ... inclusiv pentru căutare, salvare, distribuire",
Legea nr. 202/1998). This project calls e-Monitor's day list **once a day**, honestly identified,
without cookies or session tricks, never following redirects, never downloading the PDFs, and it
stops on any refusal. That list only **confirms** the "M.Of. nr. X din ..." the Chamber's pages
report for each law. Readers are linked to e-Monitor for the official text.

## Layout

| Path | What |
|---|---|
| `collector/legi/cdep.py` | parsers (pure functions, tested on real saved pages) |
| `collector/legi/http.py` | polite HTTP client (5 s interval, request budget, retries, CAPTCHA stop, cache) |
| `collector/legi/store.py` | one JSON per law, written only on change |
| `collector/legi/monitor.py` | Monitorul Oficial day list (one call per day) |
| `collector/legi/cli.py` | `update` (incremental), `mo-check`, `report --since YYYY-MM-DD` |
| `collector/tests/` | tests + real page fixtures |

```bash
cd collector
python3.12 -m venv .venv && .venv/bin/pip install --no-deps -r requirements-dev.txt
.venv/bin/pytest -q && .venv/bin/ruff check .
```
