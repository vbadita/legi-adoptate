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

## Layout

| Path | What |
|---|---|
| `collector/legi/cdep.py` | parsers (pure functions, tested on real saved pages) |
| `collector/legi/http.py` | polite HTTP client (2 s interval, retries, cache) |
| `collector/legi/cli.py` | `python -m legi adopted --since YYYY-MM-DD` |
| `collector/tests/` | tests + real page fixtures |

```bash
cd collector
python3.12 -m venv .venv && .venv/bin/pip install --no-deps -r requirements-dev.txt
.venv/bin/pytest -q && .venv/bin/ruff check .
```
