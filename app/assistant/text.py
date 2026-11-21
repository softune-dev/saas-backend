"""Turning a question (or a stored answer) into comparable tokens.

The assistant has no language model, so matching has to do its work here:
English, Bangla script and Banglish ("dam koto", "delivery charge koto") are
all reduced to the same small set of concept tokens (price, delivery, return,
payment...), so a question typed in any of them lands on the same stored
answer. Words that are not a known concept are kept as light-stemmed tokens,
which is what lets product names ("Nova", "panjabi") match.

The same function runs over the merchant's stored text and over the
customer's question. That symmetry is the point: change a synonym here and
both sides move together, so nothing needs reindexing logic of its own.
"""

import re

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_BENGALI = re.compile(r"[ঀ-৿]")
# Keep letters/digits of any script; everything else becomes a space. The
# whole Bengali block is kept explicitly: its vowel signs (ি ে ু ...) are
# combining marks, which \w does NOT match, so without this every Bangla
# word is chopped into pieces at its vowel signs.
_NON_WORD = re.compile(r"[^\w\sঀ-৿]|_", re.UNICODE)

# concept -> words that mean it. English, Banglish and Bangla together.
_CONCEPTS: dict[str, list[str]] = {
    "price": ["price", "cost", "rate", "charge", "charges", "fee", "fees", "চার্জ", "ফি", "dam", "daam", "dor", "mullo", "mulyo", "taka", "tk", "bdt", "দাম", "মূল্য", "দর", "টাকা", "৳"],
    "howmuch": ["koto", "kto", "kot", "কত"],
    "stock": ["stock", "available", "availability", "avail", "ache", "ase", "achhe", "pabo", "pawa", "আছে", "পাওয়া", "পাব", "স্টক"],
    "delivery": ["delivery", "deliver", "shipping", "ship", "courier", "dispatch", "pathabo", "pathan", "pouchabe", "ডেলিভারি", "ডেলিভারী", "কুরিয়ার", "পৌঁছ", "পাঠাব", "পাঠান", "শিপিং"],
    "day": ["day", "days", "din", "dine", "time", "koydin", "দিন", "দিনে", "সময়"],
    "return": ["return", "refund", "exchange", "replace", "replacement", "ferot", "ferat", "ফেরত", "রিটার্ন", "এক্সচেঞ্জ", "বদল", "রিফান্ড"],
    "payment": ["payment", "pay", "bkash", "nagad", "rocket", "card", "cod", "cash", "পেমেন্ট", "বিকাশ", "নগদ", "ক্যাশ", "রকেট", "কার্ড"],
    "discount": ["discount", "offer", "sale", "coupon", "promo", "deal", "off", "chhar", "chad", "ছাড়", "অফার", "ডিসকাউন্ট", "সেল", "কুপন"],
    "contact": ["contact", "call", "phone", "number", "whatsapp", "email", "mobile", "যোগাযোগ", "ফোন", "নাম্বার", "নম্বর", "হোয়াটসঅ্যাপ", "ইমেইল", "মোবাইল"],
    "location": ["address", "location", "where", "shop", "showroom", "outlet", "branch", "kothay", "thikana", "ঠিকানা", "কোথায়", "শোরুম", "দোকান"],
    "size": ["size", "sizes", "shaiz", "সাইজ", "মাপ"],
    "color": ["color", "colour", "colors", "colours", "rong", "রং", "রঙ", "কালার"],
    "warranty": ["warranty", "guarantee", "ওয়ারেন্টি", "গ্যারান্টি"],
    "track": ["track", "tracking", "status", "ট্র্যাক", "ট্র্যাকিং"],
    "order": ["order", "buy", "purchase", "kinbo", "kinte", "অর্ডার", "কিনব", "কিনতে", "কিনা"],
    "about": ["about", "story", "সম্পর্কে"],
    "hello": ["hi", "hello", "hey", "salam", "assalamu", "assalamualaikum", "হাই", "হ্যালো", "সালাম", "আসসালামু"],
    "thanks": ["thanks", "thank", "thx", "dhonnobad", "ধন্যবাদ", "শুকরিয়া"],
}

_WORD_TO_CONCEPT: dict[str, str] = {w: c for c, words in _CONCEPTS.items() for w in words}
# Bangla words take endings ("ডেলিভারিতে"), so Bangla keys also match by prefix.
_BN_PREFIXES: list[tuple[str, str]] = sorted(
    ((w, c) for w, c in _WORD_TO_CONCEPT.items() if _BENGALI.search(w) and len(w) >= 3),
    key=lambda x: -len(x[0]),
)

# Filler that carries no meaning for matching.
_STOP = set(
    "a an the is are am do does did i me my we you your it its to of in on for with and or can will be "
    "how what which who why when there this that from at by as have has please any "
    "kore korte kemon ki kno keno amar ami apnar apni jonno er ta te ke naki hobe hoy kora jabe jay lagbe korben korbo koren dibo den dite parbo parben kon koyta ache? "
    "কি কী কিভাবে কীভাবে আমি আমার আপনার আপনাদের আপনি এর ও এবং একটি যায় যাবে না হবে হয় জন্য থেকে কে তে এ টা টি গুলো কোন করা করব করবো করতে করবেন পারি পারব পারবো পারেন দিন্ কিছু এখন কোনো".split()
)

# Words so common in a store's own text that they would drown real matches.
GENERIC = {"store", "shop", "product", "products", "item", "items", "site", "website", "online", "bangladesh"}


def lang_of(text: str) -> str:
    """'bn' if the question contains Bangla script, else 'en' (Banglish counts
    as English: the reply language follows the script the customer typed)."""
    return "bn" if _BENGALI.search(text or "") else "en"


def _stem(word: str) -> str:
    if word.isascii() and len(word) > 3:
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
    return word


def _concept(word: str) -> str | None:
    if word in _WORD_TO_CONCEPT:
        return _WORD_TO_CONCEPT[word]
    if not word.isascii():
        for key, concept in _BN_PREFIXES:
            if word.startswith(key):
                return concept
    return None


def tokens(text: str) -> list[str]:
    """Concept tokens for known words, light-stemmed words for the rest."""
    cleaned = _NON_WORD.sub(" ", (text or "").lower().translate(_BN_DIGITS))
    out: list[str] = []
    for raw in cleaned.split():
        if raw in _STOP:
            continue
        concept = _concept(raw)
        if concept:
            out.append(concept)
            continue
        out.append(_stem(raw))
    return out


def concepts_in(toks: list[str]) -> set[str]:
    return {t for t in toks if t in _CONCEPTS}


def content_tokens(toks: list[str]) -> list[str]:
    """Tokens that identify something specific (product, category), i.e. not a
    concept and not generic store vocabulary."""
    return [t for t in toks if t not in _CONCEPTS and t not in GENERIC and len(t) > 1]
