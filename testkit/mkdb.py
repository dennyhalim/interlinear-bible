"""Toy database with the real table/column names. build(path, shift=0): shift>0 inserts extra words first,
so word_ids move (simulates a rebuild). Layout: GEN 1:1-4, GEN 2:1-2, EXO 1:1, MAT 3:1 (3 words each; GEN/EXO Hebrew, MAT Greek)."""
import sqlite3, sys

SCHEMA = """
create table book(book_id integer primary key, code text, name text, ordinal integer, testament text);
create table verse(verse_id integer primary key, book_id integer, chapter integer, verse integer);
create table word(word_id integer primary key, verse_id integer, position integer, language text, surface text,
                  translit text, strongs text, strongs_parts text, morph_code text, gloss_source text);
create table lexicon_entry(lexicon_id integer primary key, dstrong text, estrong text, gloss text);
create table lexicon_gloss_translation(lexicon_id integer, language text, gloss text);
create table ai_gloss(gloss_id integer primary key, word_id integer unique, gloss text, alt_glosses text, note text,
                      prompt_version text, model text, generated_at text);
insert into book values(1,'GEN','Genesis',1,'OT'),(2,'EXO','Exodus',2,'OT'),(3,'MAT','Matthew',40,'NT');
insert into lexicon_entry values(1,'H5002','H5002','utterance'),(2,'H3068G','H3068','LORD'),(3,'H0113','H0113','lord'),
                                  (4,'G0907','G0907','to baptize'),(5,'G3056','G3056','word');
insert into lexicon_gloss_translation values(1,'id','ucapan'),(2,'id','TUHAN'),(3,'id','tuan'),(4,'id','membenamkan'),(5,'id','firman');
"""

def build(path, shift=0):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    wid = 100 if shift else 0
    if shift:   # extra words in an early, extra verse -> every later word_id moves
        c.execute("insert into verse values(99,1,9,9)")
        for p in range(shift):
            c.execute("insert into word values(?,?,?,?,?,?,?,?,?,?)", (p + 1, 99, p + 1, "hebrew", "x", "x", "H0001", '["H0001"]', "HN", None))
        wid = shift
    layout = [(1, 1, v) for v in (1, 2, 3, 4)] + [(1, 2, v) for v in (1, 2)] + [(2, 1, 1), (3, 3, 1)]
    hebrew = ["H5002", "H3068G", "H0113"]; greek = ["G0907", "G3056", "G0907"]
    vid = 0
    for b, ch, v in layout:
        vid += 1
        c.execute("insert into verse values(?,?,?,?)", (vid, b, ch, v))
        for p in (1, 2, 3):
            wid += 1
            st = (greek if b == 3 else hebrew)[p - 1]
            c.execute("insert into word values(?,?,?,?,?,?,?,?,?,?)",
                      (wid, vid, p, "greek" if b == 3 else "hebrew", f"s{b}{ch}{v}{p}", "t", st, f'["{st}"]', "HN", None))
    c.commit()
    c.close()

if __name__ == "__main__":
    build(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0)
