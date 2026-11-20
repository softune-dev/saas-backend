"""Variants and descriptions for the second batch of demo stores. Same
shape as product_details.py, which merges this in."""

GOLD = [("Gold plated", "#D4AF37"), ("Rose gold plated", "#C98F7A")]
SILVER = ("Silver tone", "#C9CCD1")
RING = ["Adjustable", "6", "7", "8", "9"]
BANGLE = ["2.4", "2.6", "2.8"]
FOOT = ["39", "40", "41", "42", "43", "44"]
WFOOT = ["35", "36", "37", "38", "39", "40"]


def P(v, d, h, s):
    return {"v": v, "d": d, "h": h, "s": s}


DETAILS_B2 = {
    "swarna": {
        "Lakshmi Gold Choker": P(
            [("Stone", [("Ruby red", "#9B111E"), ("Emerald green", "#1F6B52"), ("Pearl white", "#F2EBDD")])],
            "A 22k gold-plated choker set with ruby-red stones and fine temple detailing. Sits close to the neck and photographs beautifully with a saree or lehenga.",
            ["22k gold plating over brass", "Hand-set stones", "Adjustable back thread", "Comes in a velvet box"],
            [("Base metal", "Brass"), ("Plating", "22k gold"), ("Weight", "48 g"), ("Care", "Keep dry, store in the box")]),
        "Dainty Pendant Chain": P(
            [("Color", GOLD + [SILVER]), ("Length", ["16 in", "18 in", "20 in"])],
            "A fine everyday chain with a tiny teardrop pendant. Light enough to forget you are wearing it, and it layers well with longer chains.",
            ["Tarnish-resistant finish", "Lobster clasp", "Gift pouch included"],
            [("Pendant", "8 mm"), ("Plating", "18k gold")]),
        "Temple Jhumka Earrings": P(
            [("Size", [("Small", -300), ("Medium", 0), ("Large", 400)])],
            "Gold-plated temple jhumkas with clusters of pearl drops that move as you do. A classic pair for weddings, Pohela Boishakh and Eid.",
            ["Push-back posts with ear chain", "Faux pearl drops", "Lightweight for long wear"],
            [("Drop length", "5 to 7 cm"), ("Plating", "22k gold")]),
        "Pearl Stud Earrings": P(
            [("Pearl size", ["6 mm", "8 mm", "10 mm"]), ("Post", GOLD + [SILVER])],
            "Round freshwater-look pearls on hypoallergenic posts. The simplest earring there is, and the one that goes with everything.",
            ["Nickel-free posts", "Lustrous shell pearls", "Gift box"],
            [("Pearl", "Shell pearl")]),
        "Filigree Bangle Pair": P(
            [("Size", BANGLE)],
            "Two bangles with hand-finished filigree, the fine open metalwork Bengal has been known for for centuries.",
            ["Set of two", "Hand-finished filigree", "Screw-open clasp"],
            [("Plating", "22k gold"), ("Width", "1.2 cm")]),
        "Slim Stacking Bangles (Set of 6)": P(
            [("Color", GOLD + [("Mixed metals", "#BFA27A")]), ("Size", BANGLE)],
            "Six thin polished bangles to stack together or split between both wrists. They chime softly when you move.",
            ["Set of six", "High-polish finish", "Stackable"],
            [("Width", "2 mm each")]),
        "Kundan Cocktail Ring": P(
            [("Stone", [("White kundan", "#F2EBDD"), ("Pink", "#E58FA8"), ("Green", "#1F6B52")])],
            "A statement ring with a large kundan centre stone ringed by tiny pearls. The adjustable band fits most fingers.",
            ["Adjustable band", "Hand-set kundan", "Pearl border"],
            [("Face", "2.5 cm"), ("Plating", "22k gold")]),
        "Minimal Band Ring": P(
            [("Color", GOLD + [SILVER]), ("Size", RING)],
            "A smooth, slightly domed band for every day. Wear one alone or stack a few.",
            ["Comfort-fit inside", "Tarnish-resistant", "Stackable"],
            [("Width", "3 mm")]),
        "Rani Bridal Set": P(
            [("Stone", [("Ruby red", "#9B111E"), ("Emerald green", "#1F6B52")])],
            "A complete bridal set: a layered rani necklace, matching earrings, maang tikka and nath. Made to be the centre of the wedding photos.",
            ["Necklace, earrings, tikka and nath", "Hand-set stones and pearls", "Wooden jewellery box"],
            [("Pieces", "4"), ("Plating", "22k gold"), ("Weight", "210 g")]),
        "Rose Kundan Bridal Set": P(
            [("Stone", [("Rose pink", "#E58FA8"), ("Mint", "#9FD8C8")])],
            "Kundan and soft pink stones in a necklace and earring set for holud, mehendi and reception looks.",
            ["Necklace and earrings", "Adjustable thread back", "Velvet box"],
            [("Pieces", "3"), ("Plating", "22k gold")]),
        "Sterling Silver Anklet": P(
            [("Length", ["9 in", "10 in", "11 in"]), ("Pack", [("Single", 0), ("Pair", 1400)])],
            "A 925 sterling silver anklet with tiny bells that ring softly as you walk.",
            ["925 sterling silver", "Extension chain", "Hallmarked"],
            [("Metal", "925 silver")]),
        "Silver Hoop Earrings": P(
            [("Size", [("2 cm", -200), ("3 cm", 0), ("4 cm", 250)])],
            "Polished 925 silver hoops with a click-shut hinge. Light, comfortable, and fine for sensitive ears.",
            ["925 sterling silver", "Hinged click closure", "Hypoallergenic"],
            [("Metal", "925 silver")]),
    },
    "oudh": {
        "Noir Amber EDP 100ml": P(
            [("Size", [("50ml", -1800), ("100ml", 0)])],
            "A warm evening scent: amber and vanilla up front, settling into smoked wood. Lasts through a long dawat.",
            ["Eau de parfum, 8 to 10 hours", "Unisex", "Gift box"],
            [("Top", "Bergamot, pink pepper"), ("Heart", "Amber, vanilla"), ("Base", "Smoked wood, musk")]),
        "Rose Royale EDP 100ml": P(
            [("Size", [("50ml", -1700), ("100ml", 0)])],
            "Damask rose deepened with saffron and soft musk. Elegant by day, rich enough for weddings.",
            ["Eau de parfum, 8 hours", "Long-lasting sillage", "Gift box"],
            [("Top", "Saffron"), ("Heart", "Damask rose"), ("Base", "White musk")]),
        "White Musk Attar 12ml": P(
            [("Size", [("6ml", -400), ("12ml", 0), ("24ml", 750)])],
            "A clean, soft musk attar that sits close to the skin. Alcohol-free, so it suits prayer.",
            ["Alcohol-free", "Roll-on bottle", "6 to 8 hours"],
            [("Type", "Oil-based attar")]),
        "Majmua Attar 12ml": P(
            [("Size", [("6ml", -450), ("12ml", 0), ("24ml", 850)])],
            "The classic subcontinental blend of khus, rose and sandal: earthy, cooling and calm.",
            ["Alcohol-free", "Traditional blend", "Roll-on bottle"],
            [("Notes", "Khus, rose, sandalwood")]),
        "Cambodian Oud Oil 3ml": P(
            [("Size", [("3ml", 0), ("6ml", 3200)])],
            "Pure Cambodian oud oil: deep, smoky and slightly sweet. A single drop lasts all day.",
            ["100% pure oud", "Glass dipper bottle", "12+ hours"],
            [("Origin", "Cambodia")]),
        "Oud Wood Blend 12ml": P(
            [("Size", [("6ml", -800), ("12ml", 0)])],
            "Oud softened with creamy sandalwood, an easy first oud for everyday wear.",
            ["Alcohol-free", "Roll-on", "8 hours"],
            [("Notes", "Oud, sandalwood, amber")]),
        "Royal Bakhoor 50g": P(
            [("Scent", ["Royal", "Rose", "Oud"]), ("Weight", [("50g", 0), ("100g", 1100)])],
            "Sweet, smoky bakhoor chips to scent the home, clothes and hair the traditional way.",
            ["Long-burning chips", "Fills a room in minutes", "Sealed tin"],
            [("Use with", "Charcoal or electric burner")]),
        "Brass Bakhoor Burner": P(
            [("Finish", ["Antique brass", "Polished gold"])],
            "A hand-finished brass mabkhara with a heat-proof handle. Beautiful on a side table even when unlit.",
            ["Solid brass", "Heat-proof base", "Handmade"],
            [("Height", "18 cm")]),
        "Discovery Set (6 x 3ml)": P(
            [("Collection", ["Classic attars", "Oud collection"])],
            "Six of our signature scents in 3ml bottles. Try them all and find your favourite before buying a full size.",
            ["6 x 3ml", "Gift-ready box", "Great for travel"],
            [("Bottles", "6")]),
        "His & Hers Gift Box": P(
            [("Pair", ["Noir Amber + Rose Royale", "Oud Wood + White Musk"])],
            "Two 50ml perfumes in a keepsake box, a ready gift for weddings and anniversaries.",
            ["2 x 50ml", "Keepsake box", "Gift message on request"],
            [("Contents", "2 perfumes")]),
        "Amber Reed Diffuser": P(
            [("Scent", ["Amber", "Oud", "Jasmine"]), ("Size", [("100ml", 0), ("200ml", 900)])],
            "A reed diffuser that scents a room quietly for up to eight weeks. No flame, no plug.",
            ["Up to 8 weeks", "8 rattan reeds", "Glass bottle"],
            [("Lasts", "6 to 8 weeks")]),
        "Oud Room Spray": P(
            [("Scent", ["Oud", "Rose", "Amber"])],
            "Instant oud fragrance for living rooms, cupboards and car seats. Two sprays are enough.",
            ["Fine mist", "Fabric safe", "Long-lasting"],
            [("Size", "250ml")]),
    },
    "freshkart": {
        "Fresh Tomatoes 1kg": P(
            [("Weight", [("500g", -55), ("1kg", 0), ("2kg", 110)])],
            "Ripe red tomatoes from farms around Jessore, picked and delivered within a day.",
            ["Locally grown", "Hand-sorted", "Delivered fresh"],
            [("Origin", "Bangladesh"), ("Storage", "Room temperature")]),
        "Green Broccoli 500g": P(
            [("Weight", [("500g", 0), ("1kg", 170)])],
            "Crisp green broccoli with tight florets, washed and ready to cook.",
            ["Crisp and fresh", "Rich in fibre and vitamin C"],
            [("Storage", "Refrigerate")]),
        "Fuji Apples 1kg": P(
            [("Weight", [("1kg", 0), ("2kg", 370)])],
            "Sweet, crunchy Fuji apples, hand-picked for size and freshness.",
            ["Sweet and juicy", "Hand-picked", "Imported"],
            [("Origin", "China"), ("Storage", "Refrigerate")]),
        "Sagar Bananas (12 pcs)": P(
            [("Quantity", [("6 pcs", -65), ("12 pcs", 0), ("24 pcs", 130)])],
            "Naturally ripened Sagar bananas from Narsingdi growers, no carbide.",
            ["Naturally ripened", "Locally grown"],
            [("Origin", "Narsingdi")]),
        "Premium Miniket Rice 5kg": P(
            [("Weight", [("5kg", 0), ("10kg", 400), ("25kg", 1550)])],
            "Fine, fragrant miniket rice that cooks up soft and separate. Our best-selling everyday rice.",
            ["Sorted and cleaned", "Soft and fragrant", "Sealed bag"],
            [("Type", "Miniket")]),
        "Red Lentils (Masoor) 1kg": P(
            [("Type", ["Deshi (small)", "Bold"]), ("Weight", [("1kg", 0), ("2kg", 140)])],
            "Clean, quick-cooking masoor dal, sorted and stone-free.",
            ["Stone-free", "Cooks in 20 minutes"],
            [("Storage", "Cool, dry place")]),
        "Fresh Milk 1L": P(
            [("Size", [("500ml", -45), ("1L", 0)])],
            "Pasteurised full-cream milk, delivered chilled.",
            ["Pasteurised", "Full cream", "Delivered chilled"],
            [("Storage", "Refrigerate, use within 3 days")]),
        "Farm Eggs (12 pcs)": P(
            [("Type", [("Brown (farm)", 0), ("Deshi", 60)]), ("Quantity", [("12 pcs", 0), ("30 pcs", 230)])],
            "Fresh eggs packed in protective trays so they arrive whole.",
            ["Collected daily", "Protective tray packing"],
            [("Storage", "Refrigerate")]),
        "Butter Cookies 400g": P(
            [("Size", [("400g", 0), ("800g", 230)])],
            "Crisp butter cookies in a reusable tin, a teatime favourite and an easy gift.",
            ["Real butter", "Reusable tin"],
            [("Shelf life", "9 months")]),
        "Salted Potato Chips 150g": P(
            [("Flavour", ["Classic salted", "Masala", "Sour cream & onion"])],
            "Thin, crunchy potato chips in three favourite flavours.",
            ["Thin and crunchy", "No trans fat"],
            [("Weight", "150g")]),
        "Laundry Powder 2kg": P(
            [("Weight", [("1kg", -160), ("2kg", 0), ("5kg", 450)])],
            "Laundry powder that lifts tough stains in cold water and keeps colours bright.",
            ["Works in cold water", "Colour safe", "Fresh scent"],
            [("Use", "Hand and machine wash")]),
        "Dishwash Liquid 500ml": P(
            [("Scent", ["Lemon", "Lime & mint"]), ("Size", [("500ml", 0), ("1L", 100)])],
            "A concentrated dishwash liquid that cuts oil fast, so one drop goes a long way.",
            ["Cuts grease fast", "Gentle on hands"],
            [("Sizes", "500ml and 1L")]),
    },
    "stride": {
        "Stride Aero Runner": P(
            [("Color", [("Black", "#111111"), ("Grey", "#9CA3AF"), ("Navy", "#1E2A4A")]), ("Size", FOOT)],
            "A lightweight knit runner with a springy foam sole for daily runs, the gym and long days on your feet.",
            ["Breathable knit upper", "Cushioned foam midsole", "Grippy rubber outsole"],
            [("Upper", "Knit mesh"), ("Sole", "EVA foam, rubber"), ("Weight", "260 g")]),
        "Stride Court Classic": P(
            [("Color", [("White", "#F5F5F4"), ("White / Green", "#2F6B4F"), ("White / Navy", "#1E2A4A")]), ("Size", FOOT)],
            "A clean white leather court sneaker that goes with jeans, chinos and even a panjabi.",
            ["Genuine leather upper", "Padded collar", "Stitched cupsole"],
            [("Upper", "Leather"), ("Sole", "Rubber")]),
        "Oxford Leather Shoe": P(
            [("Color", [("Black", "#111111"), ("Brown", "#6B4226")]), ("Size", FOOT)],
            "A polished leather oxford with a cushioned insole, comfortable enough for a full office day.",
            ["Full-grain leather", "Cushioned insole", "Leather lining"],
            [("Upper", "Leather"), ("Sole", "TPR")]),
        "Suede Penny Loafer": P(
            [("Color", [("Tan", "#B07A4A"), ("Navy", "#1E2A4A"), ("Chocolate", "#4B2E1E")]), ("Size", FOOT)],
            "A soft suede penny loafer that slips on and dresses up any smart-casual look.",
            ["Soft suede", "Flexible sole", "Slip-on"],
            [("Upper", "Suede")]),
        "Cloud Comfort Slide": P(
            [("Color", [("Black", "#111111"), ("Sand", "#D8C6AA"), ("Sage", "#9CAF88")]), ("Size", FOOT)],
            "A thick, soft slide with a contoured footbed. Waterproof, so it is made for monsoon days too.",
            ["Cloud-soft EVA", "Waterproof", "Anti-slip"],
            [("Material", "EVA")]),
        "Leather Strap Sandal": P(
            [("Color", [("Brown", "#6B4226"), ("Black", "#111111")]), ("Size", FOOT)],
            "An adjustable leather strap sandal with a cushioned footbed for everyday wear.",
            ["Adjustable straps", "Cushioned footbed", "Rubber sole"],
            [("Upper", "Leather")]),
        "Block Heel Mule": P(
            [("Color", [("Blush", "#E8C4C0"), ("Black", "#111111"), ("Nude", "#D8B59A")]), ("Size", WFOOT)],
            "A comfortable 6 cm block heel mule that you can actually walk in all evening.",
            ["6 cm block heel", "Padded insole", "Slip-on"],
            [("Heel", "6 cm")]),
        "Ballet Flat": P(
            [("Color", [("Black", "#111111"), ("Beige", "#D8C6AA"), ("Red", "#B91C1C")]), ("Size", WFOOT)],
            "A soft leather ballet flat that folds into your bag and goes with everything.",
            ["Soft leather", "Flexible sole", "Cushioned heel"],
            [("Upper", "Leather")]),
        "Kids Light-Up Sneaker": P(
            [("Color", [("Blue", "#2563EB"), ("Pink", "#EC4899")]), ("Size", ["26", "28", "30", "32", "34"])],
            "Sneakers that light up with every step and close with a hook-and-loop strap kids can do themselves.",
            ["LED lights in the sole", "Hook-and-loop strap", "Non-slip sole"],
            [("Lights", "Motion activated")]),
        "Kids School Shoe": P(
            [("Size", ["28", "30", "32", "34", "36", "38"])],
            "A tough black school shoe with a scuff-resistant toe and a sole that lasts the term.",
            ["Scuff-resistant toe", "Velcro strap", "Durable sole"],
            [("Color", "Black")]),
        "Sneaker Cleaning Kit": P(
            [("Kit", [("Basic", 0), ("Pro (with protector spray)", 400)])],
            "Foam cleaner, a soft brush and a microfibre cloth: everything to bring white sneakers back.",
            ["Works on leather, knit and canvas", "Soft-bristle brush", "Microfibre cloth"],
            [("Cleaner", "150ml")]),
        "Cotton Ankle Socks (5 pairs)": P(
            [("Color", [("White", "#F5F5F4"), ("Black", "#111111"), ("Mixed", "#9CA3AF")]), ("Size", ["Free size (39-44)", "Small (35-38)"])],
            "Breathable cotton ankle socks with a cushioned sole and a heel grip so they do not slip down.",
            ["Cotton blend", "Cushioned sole", "5 pairs"],
            [("Material", "80% cotton")]),
    },
}
