"""Background statistics for the topic profiles (part D).

    .venv/bin/python scripts/build_background.py --fetch    # download the 58 public XMLs to data/background/ first
    .venv/bin/python scripts/build_background.py            # spaCy pass over the background corpus + TSVs
    .venv/bin/python scripts/build_background.py --from-cache   # only re-derive the TSVs (thresholds changed)

Background corpus: the 58 Bundestag sittings listed in data/background_sittings.txt (public Plenarprotokoll XML from
dserver.bundestag.de, saved in data/background/, which is not part of the repository).
Excluded: the three test sittings 21030, 21032, 21085 (never read), and also the four dev sittings 21055, 21098,
21099, 21100, so that the dev evaluation runs under the same condition as the final test run (the item's own
debate is never part of its background).

Only member/government speech is counted (chair turns are procedure). Doc = one speech (one <rede>, or a run of
paragraphs of one speaker where the XML has no rede id, as in the Fragestunde).

Outputs (data/resources/):
  background_lemmas.tsv   lemma, pos, freq, df (speeches), dp, range      (lemmas with freq >= MIN_FREQ)
  bt_genre_words.tsv      lemma, dp, range, freq, source                  (Bundestag genre vocabulary)

Dispersion: Gries' DP (Gries 2008) over the gold agenda blocks of all background sittings. DP = 0 means the lemma is
spread over the blocks exactly in proportion to their size; DP near 1 means it is concentrated in few blocks.
Genre words are frequent lemmas with low DP and high range: they occur in nearly every debate whatever its topic
("Kollege", "Präsident", "heute", "Antrag"). Topic words have high DP ("Glasfaser", "Rente").
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from niederschrift.sources import parse_xml  # noqa: E402
from niederschrift.topic import lemma_of, nlp  # noqa: E402

BG = ROOT / "data" / "background"
BG_LIST = ROOT / "data" / "background_sittings.txt"
RES = ROOT / "data" / "resources"
CACHE = ROOT / "work" / "debate" / "background_counts.json"
NEVER = {"21030", "21032", "21085"}                 # test sittings: never read
PROJECT = {"21055", "21098", "21099", "21100"}      # dev sittings of this project: kept out of the background
MIN_FREQ = 5
GENRE_POS = {"NOUN", "PROPN", "ADJ", "VERB", "ADV"}
GENRE = {"min_freq": 150, "max_dp": 0.40, "min_range": 0.30}
MUENSTER_MAX_DP = 0.55       # a Münster genre word is kept only if it is not clumped (topical) in the Bundestag


def background_files():
    files = {sid: BG / f"{sid}.xml" for sid in background_ids() if (BG / f"{sid}.xml").exists()}
    return {s: p for s, p in files.items() if s not in NEVER and s not in PROJECT}


def background_ids():
    lines = [l for l in BG_LIST.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return [x for l in lines for x in l.split()]


def fetch():
    """Download the public Plenarprotokoll XMLs of the background corpus (about 50 MB)."""
    import urllib.request
    BG.mkdir(parents=True, exist_ok=True)
    for sid in background_ids():
        out = BG / f"{sid}.xml"
        if not out.exists():
            url = f"https://dserver.bundestag.de/btp/{sid[:2]}/{sid}.xml"
            out.write_bytes(urllib.request.urlopen(url, timeout=60).read())
            print("fetched", sid, file=sys.stderr)
            time.sleep(0.5)


def speeches_of(units):
    """Doc = one speech: group non-chair units by rede id, or by runs of one speaker where rede is None."""
    out, last = [], None
    for u in units:
        if u["role"] == "chair":
            if isinstance(last, tuple):          # a chair turn ends a run of a rede-less speaker
                last = None
            continue
        key = u["rede"] or ("run", u["speaker"])
        if out and key == last:
            out[-1].append(u)
        else:
            out.append([u])
        last = key
    return out


def count(files):
    nl = nlp()
    freq, df, pos = Counter(), Counter(), defaultdict(Counter)
    block_freq, block_tok = defaultdict(Counter), Counter()
    n_tok = n_speech = 0
    t0 = time.time()
    for n, (sid, path) in enumerate(sorted(files.items())):
        meta, units, gold = parse_xml(path)
        ub = gold["unit_block"]
        sp = speeches_of(units)
        texts = [u["text"] for s in sp for u in s]
        owners = [(k, u["i"]) for k, s in enumerate(sp) for u in s]
        seen = [set() for _ in sp]
        for (k, i), doc in zip(owners, nl.pipe(texts, batch_size=128, disable=["parser", "ner"])):
            blk = ub.get(i)
            bkey = f"{sid}:{blk}" if blk is not None else None
            for t in doc:
                lem = lemma_of(t)
                if not lem:
                    continue
                n_tok += 1
                freq[lem] += 1
                pos[lem][t.pos_] += 1
                seen[k].add(lem)
                if bkey:
                    block_freq[bkey][lem] += 1
                    block_tok[bkey] += 1
        for s in seen:
            df.update(s)
        n_speech += len(sp)
        print(f"{n + 1}/{len(files)} {sid}: {len(sp)} speeches, {n_tok} tokens so far, {time.time() - t0:.0f}s",
              flush=True)
    return {"freq": freq, "df": df, "pos": {k: v.most_common(1)[0][0] for k, v in pos.items()},
            "block_freq": {k: dict(v) for k, v in block_freq.items()}, "block_tok": dict(block_tok),
            "tokens": n_tok, "speeches": n_speech, "sittings": sorted(files)}


def dispersion(c):
    """Gries DP and range for every lemma over the background's agenda blocks."""
    total = sum(c["block_tok"].values())
    share = {b: n / total for b, n in c["block_tok"].items()}
    occ = defaultdict(dict)
    for b, cnt in c["block_freq"].items():
        for lem, f in cnt.items():
            occ[lem][b] = f
    n_blocks = len(share)
    dp, rng = {}, {}
    for lem, parts in occ.items():
        f = sum(parts.values())
        # sum over all blocks |v_i - s_i|; blocks without the lemma contribute s_i
        s = 1.0
        for b, fb in parts.items():
            s += abs(fb / f - share[b]) - share[b]
        dp[lem] = 0.5 * s
        rng[lem] = len(parts) / n_blocks
    return dp, rng, n_blocks


def write(c):
    dp, rng, n_blocks = dispersion(c)
    RES.mkdir(parents=True, exist_ok=True)
    rows = sorted(((lem, f) for lem, f in c["freq"].items() if f >= MIN_FREQ), key=lambda x: (-x[1], x[0]))
    head = [f"# Background lemma statistics, {len(c['sittings'])} Plenarprotokolle (see scripts/build_background.py)",
            f"# tokens = {c['tokens']}", f"# speeches = {c['speeches']}", f"# blocks = {n_blocks}",
            f"# sittings: {' '.join(c['sittings'])}",
            f"# lemmas with freq >= {MIN_FREQ}; df = speeches containing the lemma; dp = Gries DP over agenda blocks",
            "lemma\tpos\tfreq\tdf\tdp\trange"]
    lines = [f"{lem}\t{c['pos'].get(lem, '')}\t{f}\t{c['df'].get(lem, 0)}\t{dp.get(lem, 1.0):.3f}\t{rng.get(lem, 0):.3f}"
             for lem, f in rows]
    (RES / "background_lemmas.tsv").write_text("\n".join(head + lines) + "\n", encoding="utf-8")

    genre = {}
    for lem, f in rows:
        if c["pos"].get(lem) in GENRE_POS and f >= GENRE["min_freq"] and dp.get(lem, 1) <= GENRE["max_dp"] \
                and rng.get(lem, 0) >= GENRE["min_range"]:
            genre[lem] = (dp[lem], rng[lem], f, "bt")
    # Münster genre stoplist (kind=content): word forms -> lemmas; kept unless clumped (topical) in the Bundestag
    added = rejected = 0
    mpath = RES / "genre_stoplist_muenster.txt"
    words = [ln.split("\t")[0] for ln in mpath.read_text(encoding="utf-8").splitlines()
             if not ln.startswith("#") and ln.endswith("\tcontent")]
    nl = nlp()
    for w in words:
        lem = lemma_of(nl(w)[0]) or w
        for cand in {lem, w}:
            d = dp.get(cand)
            if d is not None and d > MUENSTER_MAX_DP:
                rejected += 1
                continue
            if cand in genre:
                genre[cand] = genre[cand][:3] + ("bt+muenster",)
            else:
                genre[cand] = (dp.get(cand, float("nan")), rng.get(cand, 0.0), c["freq"].get(cand, 0), "muenster")
                added += 1
    ghead = [f"# Bundestag genre lemmas: pos in {sorted(GENRE_POS)}, freq >= {GENRE['min_freq']}, "
             f"DP <= {GENRE['max_dp']}, range >= {GENRE['min_range']} over {n_blocks} agenda blocks;",
             f"# plus the Münster genre stoplist (kind=content), lemmatised, unless DP > {MUENSTER_MAX_DP} in the "
             f"Bundestag background ({rejected} forms rejected as topical here).",
             "lemma\tdp\trange\tfreq\tsource"]
    glines = [f"{lem}\t{v[0]:.3f}\t{v[1]:.3f}\t{v[2]}\t{v[3]}" for lem, v in sorted(genre.items(), key=lambda x: x[1][0])]
    (RES / "bt_genre_words.tsv").write_text("\n".join(ghead + glines) + "\n", encoding="utf-8")
    print(f"background_lemmas.tsv: {len(lines)} lemmas; bt_genre_words.tsv: {len(glines)} lemmas "
          f"({sum(1 for v in genre.values() if v[3] == 'bt')} bt only, {added} from Münster)")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--from-cache", action="store_true")
    a.add_argument("--fetch", action="store_true")
    x = a.parse_args()
    if x.fetch:
        fetch()
    if x.from_cache and CACHE.exists():
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        c["freq"], c["df"] = Counter(c["freq"]), Counter(c["df"])
    else:
        files = background_files()
        assert not (set(files) & (NEVER | PROJECT))
        c = count(files)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    write(c)


if __name__ == "__main__":
    main()
