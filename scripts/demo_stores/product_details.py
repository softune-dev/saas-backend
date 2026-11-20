"""Variants and full descriptions for the demo stores' products, keyed by
store slug, then product name (as in catalog.py).

Each entry:
  v  variant types. ("Size", ["S", "M"]) is a plain list of labels.
     ("Color", [("Sand", "#C8B49A"), ...]) is a colour: a swatch hex per value.
     ("Storage", [("128GB", 0), ("256GB", 3000)]) changes the price: the
     number is taka added to the base price.
  d  opening paragraph
  h  highlights
  s  specifications as (label, value)

seed.py turns this into Product.attributes (variants + combinations, run
through app.products.validate_attributes like any dashboard write) and an
HTML description, which every theme already renders.
"""

SIZES = ["S", "M", "L", "XL"]
FREE = ["Free size"]
ABAYA = ["52", "54", "56", "58"]
PANJABI = ["38", "40", "42", "44", "46"]
KIDS = ["0-6M", "6-12M", "1-2Y", "2-3Y", "3-4Y"]
SHOE = ["39", "40", "41", "42", "43", "44"]

DETAILS: dict[str, dict[str, dict]] = {
    "noor": {
        "Sand Pleated Abaya": {
            "v": [("Color", [("Sand", "#C8B49A"), ("Black", "#1C1C1C"), ("Dusty Rose", "#C99A9A")]), ("Size", ABAYA)],
            "d": "An everyday abaya in lightweight crepe that falls in soft knife pleats from the yoke. Wide sleeves keep it airy in the heat, and the fabric doesn't cling or crease in your bag.",
            "h": ["Soft pleats from yoke to hem", "Wide sleeves with a neat cuff", "Hidden side pockets", "Pairs with any of our chiffon or jersey hijabs"],
            "s": [("Fabric", "Premium crepe"), ("Fit", "Relaxed"), ("Length", "52 to 58 inches"), ("Care", "Machine wash cold, hang dry")],
        },
        "Midnight Open Abaya": {
            "v": [("Color", [("Charcoal", "#2E2E33"), ("Navy", "#1F2A44"), ("Black", "#111111")]), ("Size", ABAYA)],
            "d": "A front-open abaya that works over a dress, a co-ord or jeans. The satin trim along the front and cuffs catches the light just enough for evenings and dawat.",
            "h": ["Open front with satin trim", "Belt loops and a matching tie belt", "Medium weight, opaque", "Easy to layer all year"],
            "s": [("Fabric", "Nida"), ("Trim", "Satin"), ("Length", "52 to 58 inches"), ("Care", "Gentle wash, iron low")],
        },
        "Ivory Chiffon Hijab": {
            "v": [("Color", [("Ivory", "#F4EEE1"), ("Blush", "#E8C4C0"), ("Taupe", "#9C8A7A"), ("Black", "#151515")])],
            "d": "Pleat-free chiffon that drapes cleanly and lets air through. It holds a pin without snagging and doesn't need ironing after a wash.",
            "h": ["Breathable, semi-sheer", "Stitched edges, no fraying", "Pin-friendly weave"],
            "s": [("Size", "180 x 70 cm"), ("Fabric", "Premium chiffon"), ("Care", "Hand wash")],
        },
        "Mocha Jersey Hijab": {
            "v": [("Color", [("Mocha", "#7B5E4B"), ("Sage", "#9CAF88"), ("Grey", "#8A8A8A"), ("Black", "#151515")])],
            "d": "A stretchy cotton jersey hijab that stays where you put it, from the morning commute to the last class of the day. No pins needed for most styles.",
            "h": ["Soft stretch, non-slip", "Opaque and breathable", "Ideal for school, work and sports"],
            "s": [("Size", "170 x 60 cm"), ("Fabric", "95% cotton, 5% spandex"), ("Care", "Machine wash")],
        },
        "Olive Linen Kaftan": {
            "v": [("Color", [("Olive", "#6B7344"), ("Sand", "#C8B49A"), ("White", "#F7F5F0")]), ("Size", FREE + ["Plus"])],
            "d": "A relaxed linen kaftan with a tie at the waist so you can wear it loose or shaped. Linen softens with every wash and stays cool through Dhaka summers.",
            "h": ["Adjustable tie waist", "Side slits for easy movement", "Gets softer with each wash"],
            "s": [("Fabric", "Linen blend"), ("Fit", "Free size, plus size available"), ("Length", "56 inches"), ("Care", "Machine wash cold")],
        },
        "Rose Silk Kaftan": {
            "v": [("Color", [("Rose", "#C98A8E"), ("Champagne", "#E3D3B5"), ("Emerald", "#1F6B52")]), ("Size", FREE + ["Plus"])],
            "d": "Fluid satin-silk kaftan with a soft sheen, cut generously for Eid mornings and evening dawat. Light enough to wear all day, dressy enough for photos.",
            "h": ["Silky drape that doesn't cling", "Delicate hand-finished neckline", "Comes folded in a gift box"],
            "s": [("Fabric", "Satin silk"), ("Length", "57 inches"), ("Care", "Dry clean recommended")],
        },
        "Charcoal Tunic Co-ord": {
            "v": [("Color", [("Charcoal", "#36383D"), ("Beige", "#D8C6AA"), ("Maroon", "#6A1E2B")]), ("Size", SIZES)],
            "d": "A long tunic over wide-leg trousers: one decision in the morning and you're dressed. The tunic covers the hips and the trousers have an elastic back for comfort.",
            "h": ["Two-piece set", "Tunic with side slits", "Wide-leg trousers with pockets"],
            "s": [("Fabric", "Airflow crepe"), ("Tunic length", "40 inches"), ("Care", "Machine wash cold")],
        },
        "Sage Wrap Co-ord": {
            "v": [("Color", [("Sage", "#9CAF88"), ("Lilac", "#B9A7C9"), ("Cream", "#EFE6D6")]), ("Size", SIZES)],
            "d": "A wrap top and pleated midi skirt in a soft sage. Wear them together or split them: the top works with jeans, the skirt with a plain tee.",
            "h": ["Adjustable wrap top", "Pleated skirt with elastic waist", "Fully lined skirt"],
            "s": [("Fabric", "Georgette"), ("Skirt length", "38 inches"), ("Care", "Gentle wash")],
        },
        "Pearl Prayer Dress": {
            "v": [("Color", [("Pearl", "#EDE8DF"), ("Grey", "#9A9A9A"), ("Navy", "#1F2A44")]), ("Size", ["Standard", "Long"])],
            "d": "A one-piece prayer dress you slip on in seconds, with an attached hood that covers fully without pins. Folds into its matching pouch.",
            "h": ["Attached hood", "Elastic sleeve cuffs", "Matching pouch included"],
            "s": [("Fabric", "Soft jersey"), ("Care", "Machine wash")],
        },
        "Travel Prayer Set": {
            "v": [("Color", [("Sage", "#9CAF88"), ("Grey", "#9A9A9A"), ("Blush", "#E8C4C0")])],
            "d": "Prayer dress and a thin prayer mat that both fold into one pouch the size of a phone. Made for office drawers, handbags and travel.",
            "h": ["Prayer dress and mat in one pouch", "Weighs under 300 g", "Quick-dry fabric"],
            "s": [("Includes", "Dress, mat, pouch"), ("Fabric", "Parachute silk"), ("Care", "Hand wash")],
        },
        "Pearl Hijab Pins (Set of 6)": {
            "v": [("Finish", ["Gold tone", "Silver tone", "Rose gold"])],
            "d": "Six sturdy pins with pearl heads that hold a hijab in place without snagging. They come in a small tin so they don't go missing.",
            "h": ["Rust-resistant pins", "Snag-free tips", "Storage tin included"],
            "s": [("Quantity", "6 pins"), ("Length", "5.5 cm")],
        },
        "Tan Leather Tote": {
            "v": [("Color", [("Tan", "#B07A4A"), ("Black", "#1A1A1A"), ("Burgundy", "#6D2333")])],
            "d": "A structured tote that holds a folded abaya, a laptop sleeve and the day's essentials. Inner zip pocket for keys and phone.",
            "h": ["Fits a 14-inch laptop", "Zip and slip pockets inside", "Magnetic top closure"],
            "s": [("Material", "Vegan leather"), ("Size", "38 x 30 x 13 cm"), ("Strap drop", "25 cm")],
        },
    },
    "voltix": {
        "Voltix Aura ANC Headphones": {
            "v": [("Color", [("Midnight Black", "#121418"), ("Silver", "#C9CCD1"), ("Navy", "#1E2A4A")])],
            "d": "Over-ear headphones with active noise cancelling that turns down traffic, generators and fans. 40 hours of playback with ANC on, and a 10-minute charge gives 5 more hours.",
            "h": ["Hybrid ANC with transparency mode", "40-hour battery, USB-C fast charging", "Two devices connected at once", "Foldable with a hard case"],
            "s": [("Bluetooth", "5.3"), ("Drivers", "40 mm"), ("Battery", "40 hours (ANC on)"), ("Weight", "250 g"), ("Warranty", "1 year")],
        },
        "Voltix Pods Pro Earbuds": {
            "v": [("Color", [("White", "#F3F4F6"), ("Black", "#111827")])],
            "d": "True wireless earbuds with ANC and a wireless charging case. Clear calls with four mics, and a low-latency mode for games.",
            "h": ["ANC and ambient mode", "Wireless and USB-C charging", "IPX5 sweat resistant", "Touch controls"],
            "s": [("Bluetooth", "5.3"), ("Battery", "7 h buds, 30 h with case"), ("Mics", "4"), ("Warranty", "1 year")],
        },
        "Voltix Watch S2 AMOLED": {
            "v": [("Color", [("Black", "#111111"), ("Silver", "#C9CCD1"), ("Rose Gold", "#D6A28C")]), ("Strap", ["Silicone", "Metal mesh", "Leather"])],
            "d": "A bright AMOLED smartwatch that takes Bluetooth calls from your wrist and tracks heart rate, SpO2 and sleep. Notifications from WhatsApp, Messenger and SMS.",
            "h": ["1.43-inch AMOLED, always-on", "Bluetooth calling", "Heart rate, SpO2, sleep", "IP68 water resistance"],
            "s": [("Display", "1.43\" AMOLED"), ("Battery", "Up to 7 days"), ("Water rating", "IP68"), ("Warranty", "1 year")],
        },
        "Voltix Watch Active": {
            "v": [("Color", [("Black", "#111111"), ("Olive", "#5B6B47"), ("Pink", "#E7A3B4")])],
            "d": "A light sports watch with more than 100 workout modes and a 10-day battery. Built-in GPS-assisted tracking through your phone for runs and rides.",
            "h": ["100+ sport modes", "10-day battery", "5ATM water resistance"],
            "s": [("Display", "1.83\" TFT"), ("Battery", "Up to 10 days"), ("Weight", "32 g"), ("Warranty", "1 year")],
        },
        "Voltix Nova 12 (8/256GB)": {
            "v": [("Color", [("Ocean Blue", "#2563EB"), ("Graphite", "#2D2F36"), ("Mint", "#9FD8C8")]), ("Storage", [("8/256GB", 0), ("12/512GB", 6000)])],
            "d": "A 120Hz phone with a 50MP main camera and a 5000mAh battery that lasts a full day of heavy use. 67W charging gets you to 50% in about 15 minutes.",
            "h": ["6.7\" 120Hz AMOLED", "50MP OIS camera", "5000mAh with 67W charging", "Official warranty"],
            "s": [("Display", "6.7\" AMOLED, 120Hz"), ("Camera", "50MP + 8MP + 2MP"), ("Battery", "5000mAh"), ("Charging", "67W"), ("Warranty", "1 year official")],
        },
        "Voltix Nova Lite (6/128GB)": {
            "v": [("Color", [("Black", "#111111"), ("Sky Blue", "#7DB7E8"), ("Lavender", "#B9A7E0")]), ("Storage", [("6/128GB", 0), ("8/256GB", 3000)])],
            "d": "A dependable everyday phone with a smooth 90Hz screen and a big battery that lasts into a second day.",
            "h": ["6.6\" 90Hz display", "5000mAh battery", "Side fingerprint and face unlock"],
            "s": [("Display", "6.6\" IPS, 90Hz"), ("Camera", "50MP"), ("Battery", "5000mAh, 18W"), ("Warranty", "1 year official")],
        },
        "Voltix Book Air 14": {
            "v": [("Color", [("Space Grey", "#4B5059"), ("Silver", "#C9CCD1")]), ("Memory", [("16GB / 512GB", 0), ("16GB / 1TB", 8000)])],
            "d": "A thin, light laptop for office work, classes and travel. Fast SSD, a sharp 14-inch screen and a battery that lasts the working day.",
            "h": ["1.3 kg aluminium body", "Backlit keyboard", "Up to 12 hours battery", "USB-C charging"],
            "s": [("Display", "14\" 2.2K IPS"), ("RAM", "16GB"), ("Storage", "512GB / 1TB SSD"), ("Weight", "1.3 kg"), ("Warranty", "2 years")],
        },
        "Voltix Book Pro 16": {
            "v": [("Memory", [("32GB / 1TB", 0), ("32GB / 2TB", 15000)])],
            "d": "A 16-inch creator laptop with a dedicated graphics card for editing, 3D and gaming, and a colour-accurate 165Hz screen.",
            "h": ["16\" 2.5K 165Hz, 100% sRGB", "Dedicated RTX graphics", "Dual-fan cooling"],
            "s": [("Display", "16\" 2.5K, 165Hz"), ("RAM", "32GB DDR5"), ("Graphics", "RTX 4060 8GB"), ("Warranty", "2 years")],
        },
        "Voltix Boom Go Speaker": {
            "v": [("Color", [("Black", "#111111"), ("Blue", "#2563EB"), ("Red", "#DC2626")])],
            "d": "A small waterproof speaker with surprisingly big 20W sound. Pair two for stereo at picnics and rooftop evenings.",
            "h": ["IP67 waterproof and dustproof", "12-hour battery", "Stereo pairing"],
            "s": [("Output", "20W"), ("Battery", "12 hours"), ("Bluetooth", "5.3"), ("Warranty", "1 year")],
        },
        "Voltix Party Max Speaker": {
            "v": [("Color", [("Black", "#111111"), ("Blue", "#2563EB")])],
            "d": "A 60W party speaker with deep bass, light effects that follow the beat and a mic input for karaoke.",
            "h": ["60W with bass boost", "Beat-synced lights", "Mic and guitar input"],
            "s": [("Output", "60W"), ("Battery", "15 hours"), ("Inputs", "Mic, AUX, USB"), ("Warranty", "1 year")],
        },
        "Voltix PowerCell 20000mAh": {
            "v": [("Color", [("Black", "#111111"), ("White", "#F3F4F6")])],
            "d": "A 20000mAh power bank with 22.5W fast charging that charges a phone four times. Three outputs, so the whole family shares one.",
            "h": ["22.5W fast charging", "USB-C in and out, plus 2 USB-A", "Digital battery display"],
            "s": [("Capacity", "20000mAh"), ("Output", "22.5W max"), ("Weight", "380 g"), ("Warranty", "6 months")],
        },
        "Voltix 65W GaN Charger": {
            "v": [("Plug", ["BD/UK 3-pin", "2-pin"]), ("Cable", [("No cable", 0), ("With 1.5m USB-C cable", 300)])],
            "d": "A compact 65W GaN charger that powers a laptop and a phone at the same time. About half the size of a normal laptop brick.",
            "h": ["65W total, 2 USB-C + 1 USB-A", "GaN: runs cool and small", "Over-heat protection"],
            "s": [("Output", "65W max"), ("Ports", "2x USB-C, 1x USB-A"), ("Warranty", "1 year")],
        },
    },
    "tinytots": {
        "Rainbow Stacking Blocks": {
            "v": [("Set size", [("20 blocks", 0), ("40 blocks", 900)])],
            "d": "Smooth wooden blocks in soft pastel colours for stacking, sorting and building. They help little hands learn grip and balance while they play.",
            "h": ["Child-safe water-based paint", "Rounded edges", "Comes in a cotton storage bag"],
            "s": [("Material", "Pine wood"), ("Age", "12 months +"), ("Block size", "4 cm")],
        },
        "Shape Sorter Cube": {
            "v": [("Style", ["Pastel", "Bright"])],
            "d": "A wooden cube with 12 shapes to match to their holes. Teaches shapes, colours and problem solving, and the lid opens to get them all back out.",
            "h": ["12 chunky shapes", "Opening lid", "Non-toxic finish"],
            "s": [("Material", "Wood"), ("Age", "18 months +"), ("Size", "15 x 15 x 15 cm")],
        },
        "Cuddly Bunny Plush 30cm": {
            "v": [("Color", [("Pink", "#F4B6C2"), ("Cream", "#F3E9D7"), ("Grey", "#B8B8B8")]), ("Size", [("30 cm", 0), ("45 cm", 400)])],
            "d": "A super-soft bunny with long floppy ears, made to be hugged, dragged and loved. Embroidered eyes, so no small parts come loose.",
            "h": ["Embroidered eyes and nose", "Machine washable", "Safe from birth"],
            "s": [("Material", "Polyester plush"), ("Age", "0 months +")],
        },
        "Sleepy Bear Plush": {
            "v": [("Color", [("Honey", "#D9A766"), ("Brown", "#8B5E3C"), ("White", "#F5F2EC")])],
            "d": "A soft bear with a sleepy smile, the right size for a cot or a car seat. A bedtime friend that survives the washing machine.",
            "h": ["Embroidered face", "Machine washable", "Soft, hypoallergenic filling"],
            "s": [("Height", "35 cm"), ("Age", "0 months +")],
        },
        "Gentle Baby Wash 400ml": {
            "v": [("Size", [("200ml", -230), ("400ml", 0), ("800ml", 420)])],
            "d": "A tear-free head-to-toe wash, mild enough for newborn skin. pH balanced and free from parabens and sulphates.",
            "h": ["Tear-free formula", "Paraben and sulphate free", "Dermatologically tested"],
            "s": [("Skin type", "All, including sensitive"), ("Age", "Newborn +")],
        },
        "Baby Lotion 300ml": {
            "v": [("Scent", ["Fragrance free", "Mild lavender"]), ("Size", [("300ml", 0), ("500ml", 260)])],
            "d": "A light, fast-absorbing lotion that keeps baby skin soft for 24 hours without feeling greasy.",
            "h": ["24-hour moisture", "Non-greasy", "Dermatologically tested"],
            "s": [("Key ingredients", "Shea butter, glycerin"), ("Age", "Newborn +")],
        },
        "Anti-colic Bottle 250ml": {
            "v": [("Size", [("150ml", -100), ("250ml", 0)]), ("Nipple flow", ["Slow (0-3M)", "Medium (3-6M)", "Fast (6M+)"])],
            "d": "A wide-neck bottle with a vented anti-colic valve that cuts down on air swallowing, so feeds are calmer and burps come easier.",
            "h": ["Anti-colic vent", "Soft, breast-like nipple", "BPA free"],
            "s": [("Material", "PP, BPA free"), ("Dishwasher safe", "Yes")],
        },
        "Silicone Bib Set (2 pcs)": {
            "v": [("Color", [("Sage & Sand", "#9CAF88"), ("Blush & Grey", "#E8C4C0"), ("Sky & Mustard", "#8EC5E6")])],
            "d": "Two soft silicone bibs with a deep pocket that catches food before it reaches the floor. Rinse and wipe clean.",
            "h": ["Food-catcher pocket", "Adjustable neck", "Food-grade silicone"],
            "s": [("Quantity", "2 bibs"), ("Age", "6 months +")],
        },
        "Cotton Romper (2 pack)": {
            "v": [("Color", [("Sky & White", "#9CC9E8"), ("Peach & White", "#F6C1A4"), ("Mint & White", "#B5E2D3")]), ("Size", KIDS)],
            "d": "Two soft cotton rompers with snaps down the front and at the legs, so changes are quick even when baby won't keep still.",
            "h": ["100% combed cotton", "Front and leg snaps", "Tag-free neck"],
            "s": [("Fabric", "Cotton"), ("Care", "Machine wash")],
        },
        "Dino Print T-shirt": {
            "v": [("Color", [("Mint", "#B5E2D3"), ("Yellow", "#F7D774"), ("Grey", "#B8B8B8")]), ("Size", ["1-2Y", "2-3Y", "3-4Y", "4-5Y", "5-6Y"])],
            "d": "A breathable cotton tee covered in friendly dinosaurs. Soft ribbed neck that keeps its shape wash after wash.",
            "h": ["Breathable cotton", "Fade-resistant print", "Easy over-the-head fit"],
            "s": [("Fabric", "Cotton jersey"), ("Care", "Machine wash")],
        },
        "Kids Backpack Unicorn": {
            "v": [("Design", ["Unicorn", "Dinosaur", "Space"])],
            "d": "A light backpack with padded straps and enough room for books, a lunch box and a water bottle in the side pocket.",
            "h": ["Padded, adjustable straps", "Side bottle pocket", "Water-resistant fabric"],
            "s": [("Size", "32 x 25 x 12 cm"), ("Age", "3 to 8 years")],
        },
        "Steel Water Bottle 400ml": {
            "v": [("Color", [("Pink", "#F4B6C2"), ("Blue", "#8EC5E6"), ("Green", "#9CD3A8")]), ("Size", [("400ml", 0), ("600ml", 200)])],
            "d": "A double-walled steel bottle that keeps water cold through the school day. Leak-proof flip straw lid that small hands can open.",
            "h": ["Keeps cold for 12 hours", "Leak-proof straw lid", "BPA free"],
            "s": [("Material", "304 stainless steel"), ("Dishwasher safe", "Lid only")],
        },
    },
    "ghorkona": {
        "Terracotta Bud Vase Set": {
            "v": [("Finish", ["Natural terracotta", "Whitewashed"])],
            "d": "Three small handmade clay vases in different shapes for single stems and dried flowers. Each one is slightly different, made by potters in Bogura.",
            "h": ["Set of 3 handmade vases", "Glazed inside, holds water", "Every piece is unique"],
            "s": [("Material", "Terracotta"), ("Heights", "10, 13 and 16 cm")],
        },
        "Brass Photo Frame 5x7": {
            "v": [("Size", [("4x6", -200), ("5x7", 0), ("8x10", 500)])],
            "d": "A brushed brass frame with a fold-out stand and wall hook. Warm, quiet metal that suits wedding photos and family portraits.",
            "h": ["Glass front", "Stand and wall hook", "Velvet back"],
            "s": [("Material", "Brass-plated metal")],
        },
        "Jute Cushion Cover 18in": {
            "v": [("Color", [("Natural", "#C8AE84"), ("Charcoal", "#3A3A3A"), ("Terracotta", "#B5603F")]), ("Size", [("16 in", -60), ("18 in", 0), ("20 in", 90)])],
            "d": "A woven jute front with a soft cotton back and a hidden zip. Adds texture to a sofa or bed without fuss.",
            "h": ["Hidden zip", "Cotton back", "Insert not included"],
            "s": [("Material", "Jute and cotton"), ("Care", "Spot clean")],
        },
        "Knitted Throw Blanket": {
            "v": [("Color", [("Cream", "#EFE6D6"), ("Grey", "#A3A3A3"), ("Mustard", "#D4A537")])],
            "d": "A chunky knit throw for winter evenings on the sofa. Heavy enough to feel cosy, soft enough to wrap up in.",
            "h": ["Chunky hand-feel knit", "Machine washable", "Gift-ready packing"],
            "s": [("Size", "125 x 150 cm"), ("Material", "Acrylic yarn")],
        },
        "Rattan Table Lamp": {
            "v": [("Shade", ["Natural", "Dark walnut"])],
            "d": "A woven rattan shade that throws a warm, patterned glow across the room. Bulb included.",
            "h": ["Handwoven rattan shade", "Warm white LED bulb included", "Inline switch"],
            "s": [("Height", "45 cm"), ("Bulb", "E27 LED, 9W")],
        },
        "Warm Fairy Lights 10m": {
            "v": [("Length", [("5m", -250), ("10m", 0), ("20m", 550)]), ("Light", ["Warm white", "Multicolour"])],
            "d": "Thin copper-wire fairy lights for balconies, bedrooms and wedding decor. USB powered, so they run off a phone charger or power bank.",
            "h": ["8 lighting modes", "Bendable copper wire", "USB powered"],
            "s": [("LEDs", "100 per 10 m"), ("Power", "USB 5V")],
        },
        "Stoneware Dinner Set (16 pcs)": {
            "v": [("Color", [("Speckled Cream", "#EFE6D6"), ("Sage", "#9CAF88"), ("Charcoal", "#3A3A3A")]), ("Set size", [("16 pcs (4 people)", 0), ("24 pcs (6 people)", 2500)])],
            "d": "Speckled stoneware plates, bowls and mugs with a soft matte glaze. Sturdy enough for daily use, pretty enough for guests.",
            "h": ["Microwave and dishwasher safe", "Chip-resistant stoneware", "Lead free glaze"],
            "s": [("Includes", "Dinner plates, side plates, bowls, mugs")],
        },
        "Handpainted Mug Pair": {
            "v": [("Design", ["Leaf", "Floral", "Blue stripe"])],
            "d": "Two ceramic mugs painted by hand with simple leaf motifs. Good for morning cha and a thoughtful gift.",
            "h": ["Hand painted", "Microwave safe", "Gift box"],
            "s": [("Capacity", "350 ml each"), ("Quantity", "2 mugs")],
        },
        "Non-stick Fry Pan 26cm": {
            "v": [("Size", [("22 cm", -300), ("26 cm", 0), ("28 cm", 300)])],
            "d": "A granite-coated pan that releases eggs and parathas cleanly with very little oil. Works on gas, electric and induction.",
            "h": ["PFOA-free granite coating", "Induction ready", "Stay-cool handle"],
            "s": [("Material", "Aluminium, granite coating")],
        },
        "Glass Storage Jars (Set of 3)": {
            "v": [("Size", [("500ml each", 0), ("1L each", 400)])],
            "d": "Airtight glass jars with bamboo lids and silicone seals. Keep dal, rice, sugar and spices fresh and easy to see.",
            "h": ["Airtight silicone seal", "Bamboo lids", "Dishwasher safe glass"],
            "s": [("Quantity", "3 jars")],
        },
        "Ceramic Planter Duo": {
            "v": [("Color", [("White", "#F5F5F2"), ("Terracotta", "#B5603F"), ("Black", "#1C1C1C")])],
            "d": "Two matte ceramic planters with saucers and drainage holes, sized for snake plants, pothos and money plants.",
            "h": ["Drainage hole and saucer", "Matte glaze", "Indoor and balcony"],
            "s": [("Sizes", "12 cm and 15 cm")],
        },
        "Faux Olive Tree 120cm": {
            "v": [("Height", [("90 cm", -1200), ("120 cm", 0), ("150 cm", 1500)])],
            "d": "A lifelike olive tree in a weighted pot. Brings green into corners with no light and needs nothing but a dust now and then.",
            "h": ["Realistic leaves and trunk", "Weighted pot", "No watering"],
            "s": [("Material", "PE leaves, natural wood trunk")],
        },
    },
    "pawpal": {
        "Ocean Fish Cat Food 1.5kg": {
            "v": [("Weight", [("500g", -830), ("1.5kg", 0), ("3kg", 1250)])],
            "d": "Complete dry food for adult cats, made with ocean fish and omega-3 for a shiny coat. Crunchy kibble helps keep teeth clean.",
            "h": ["Real fish first ingredient", "Omega-3 and taurine", "No artificial colours"],
            "s": [("Life stage", "Adult, 1 year +"), ("Protein", "32%")],
        },
        "Chicken Gravy Pouches (12)": {
            "v": [("Flavour", ["Chicken", "Tuna", "Chicken & Liver"]), ("Pack", [("12 pouches", 0), ("24 pouches", 1000)])],
            "d": "Tender chicken pieces in gravy that even picky cats finish. Adds moisture to the diet, which helps their kidneys.",
            "h": ["Complete meal or topper", "Easy-tear pouch", "Added vitamins"],
            "s": [("Pouch size", "85 g"), ("Life stage", "Adult")],
        },
        "Chicken & Rice Dog Food 3kg": {
            "v": [("Weight", [("3kg", 0), ("10kg", 5000)])],
            "d": "Balanced daily food for adult dogs with real chicken and rice that's gentle on the stomach.",
            "h": ["Real chicken", "Easy to digest", "Glucosamine for joints"],
            "s": [("Life stage", "Adult"), ("Protein", "26%")],
        },
        "Puppy Starter 1.5kg": {
            "v": [("Weight", [("1.5kg", 0), ("3kg", 1300)])],
            "d": "Small, soft kibble for puppies from weaning to 12 months, with DHA for brain development and calcium for growing bones.",
            "h": ["Small kibble for small mouths", "DHA and calcium", "Easy to soak"],
            "s": [("Life stage", "2 to 12 months")],
        },
        "Feather Wand Cat Toy": {
            "v": [("Refill", [("Wand only", 0), ("Wand + 3 refills", 250)])],
            "d": "A flexible wand with feathers and a bell that triggers your cat's hunting instinct. Five minutes of play a day keeps indoor cats active.",
            "h": ["Flexible rod", "Feathers with a bell", "Replaceable tip"],
            "s": [("Length", "90 cm")],
        },
        "Rope Tug Dog Toy": {
            "v": [("Size", ["Small", "Medium", "Large"])],
            "d": "A tough braided cotton rope for tug of war and chewing. The fibres help clean teeth as they chew.",
            "h": ["100% cotton rope", "Helps clean teeth", "Machine washable"],
            "s": [("Material", "Cotton")],
        },
        "Donut Plush Pet Bed": {
            "v": [("Color", [("Beige", "#D8C6AA"), ("Grey", "#A3A3A3"), ("Pink", "#F4B6C2")]), ("Size", [("S (50 cm)", -500), ("M (60 cm)", 0), ("L (80 cm)", 900)])],
            "d": "A round, raised-rim bed in long plush that pets curl up and sink into. The calming shape helps anxious pets settle.",
            "h": ["Raised rim to rest their head", "Non-slip bottom", "Machine washable"],
            "s": [("Material", "Faux fur plush")],
        },
        "Cooling Pet Mat": {
            "v": [("Size", [("S (40x50 cm)", -300), ("M (50x65 cm)", 0), ("L (65x90 cm)", 600)])],
            "d": "A pressure-activated gel mat that stays cool without water, electricity or a fridge. A relief for pets during Bangladesh summers.",
            "h": ["No water or power needed", "Wipe clean", "Folds for travel"],
            "s": [("Material", "Non-toxic gel, nylon cover")],
        },
        "Oatmeal Pet Shampoo 300ml": {
            "v": [("Size", [("300ml", 0), ("500ml", 290)])],
            "d": "A soap-free oatmeal shampoo that soothes dry, itchy skin and leaves the coat soft. Safe for dogs and cats over 8 weeks.",
            "h": ["Colloidal oatmeal and aloe", "Soap and paraben free", "Light, clean scent"],
            "s": [("For", "Dogs and cats")],
        },
        "Slicker Brush": {
            "v": [("Size", ["Small", "Large"])],
            "d": "A slicker brush that lifts loose fur and works out tangles. Press the button and the bristles retract so the fur slides off.",
            "h": ["Self-cleaning button", "Rounded pin tips", "Non-slip grip"],
            "s": [("For", "Medium and long coats")],
        },
        "Padded Nylon Collar": {
            "v": [("Color", [("Red", "#DC2626"), ("Navy", "#1E3A8A"), ("Teal", "#0F766E")]), ("Size", ["XS", "S", "M", "L"])],
            "d": "A sturdy nylon collar with a soft neoprene lining and a quick-release buckle. Has a D-ring for a leash and a name tag.",
            "h": ["Soft padded lining", "Quick-release buckle", "Reflective stitching"],
            "s": [("Material", "Nylon, neoprene")],
        },
        "Steel Bowl Pair": {
            "v": [("Size", [("Small (350ml)", -100), ("Medium (700ml)", 0), ("Large (1.5L)", 200)])],
            "d": "Two stainless steel bowls in a non-slip stand, one for food and one for water. Rust-proof and easy to wash.",
            "h": ["Stainless steel", "Non-slip base", "Dishwasher safe"],
            "s": [("Quantity", "2 bowls")],
        },
    },
    "raaj": {
        "Ivory Embroidered Panjabi": {
            "v": [("Color", [("Ivory", "#F2EBDD"), ("Mint", "#BFE0D0"), ("Sky", "#B9D6EE")]), ("Size", PANJABI)],
            "d": "A cotton-silk panjabi with fine thread embroidery around the neck and placket. Light enough for summer Eid, dressy enough for a wedding.",
            "h": ["Cotton-silk blend", "Hand-finished neck embroidery", "Side pockets"],
            "s": [("Fabric", "Cotton silk"), ("Fit", "Regular"), ("Length", "42 to 46 inches"), ("Care", "Dry clean first wash")],
        },
        "Maroon Jamdani Panjabi": {
            "v": [("Color", [("Maroon", "#6A1E2B"), ("Navy", "#1F2A44"), ("Black", "#151515")]), ("Size", PANJABI)],
            "d": "A panjabi with a woven jamdani motif on the chest, made with weavers from Narayanganj. A rich, deep colour for Eid and holud nights.",
            "h": ["Woven jamdani motif", "Fabric buttons", "Soft cotton lining at the chest"],
            "s": [("Fabric", "Cotton jamdani"), ("Fit", "Regular"), ("Care", "Dry clean")],
        },
        "Gold Brocade Koti": {
            "v": [("Color", [("Gold", "#C9A24B"), ("Maroon", "#6A1E2B"), ("Bottle Green", "#1F4D3A")]), ("Size", PANJABI)],
            "d": "A brocade waistcoat with covered fabric buttons that lifts a plain panjabi for weddings and receptions.",
            "h": ["Brocade front, satin back", "Fully lined", "Two welt pockets"],
            "s": [("Fabric", "Brocade"), ("Care", "Dry clean")],
        },
        "Charcoal Linen Koti": {
            "v": [("Color", [("Charcoal", "#36383D"), ("Beige", "#D8C6AA"), ("Olive", "#6B7344")]), ("Size", PANJABI)],
            "d": "A light linen koti for daytime events, office iftar and Friday prayers. Keeps a clean line without the heat of brocade.",
            "h": ["Breathable linen", "Half lined", "Wooden buttons"],
            "s": [("Fabric", "Linen"), ("Care", "Gentle wash")],
        },
        "White Churidar Pajama": {
            "v": [("Size", ["S", "M", "L", "XL", "XXL"])],
            "d": "A soft cotton churidar with a drawstring waist and gathered legs that sits neatly under any panjabi.",
            "h": ["Drawstring waist", "Extra-long gathered legs", "Soft cotton"],
            "s": [("Fabric", "Cotton"), ("Care", "Machine wash")],
        },
        "Beige Straight Pajama": {
            "v": [("Color", [("Beige", "#D8C6AA"), ("White", "#F7F5F0"), ("Black", "#151515")]), ("Size", ["S", "M", "L", "XL", "XXL"])],
            "d": "A straight-cut cotton pajama with an elastic and drawstring waist. The most comfortable way to wear a panjabi all day.",
            "h": ["Elastic plus drawstring waist", "Side pocket", "Straight leg"],
            "s": [("Fabric", "Cotton"), ("Care", "Machine wash")],
        },
        "Indigo Block Print Fatua": {
            "v": [("Color", [("Indigo", "#2E3F72"), ("Rust", "#A0482B")]), ("Size", ["M", "L", "XL", "XXL"])],
            "d": "A short cotton fatua hand block-printed with natural indigo. Every piece has small variations from the print block.",
            "h": ["Hand block printed", "Breathable cotton", "Chest pocket"],
            "s": [("Fabric", "Cotton"), ("Care", "Wash separately, cold")],
        },
        "Olive Khadi Fatua": {
            "v": [("Color", [("Olive", "#6B7344"), ("Natural", "#E7DCC6"), ("Brown", "#7B5E4B")]), ("Size", ["M", "L", "XL", "XXL"])],
            "d": "Handwoven khadi cotton that breathes in the heat and gets softer with every wash. Easy with jeans or pajama.",
            "h": ["Handwoven khadi", "Mandarin collar", "Side slits"],
            "s": [("Fabric", "Khadi cotton"), ("Care", "Machine wash cold")],
        },
        "Kashmiri Wool Shawl": {
            "v": [("Color", [("Camel", "#B08A5B"), ("Black", "#151515"), ("Maroon", "#6A1E2B")])],
            "d": "A soft wool shawl with a woven paisley border. Warm through winter weddings and long enough to wrap properly.",
            "h": ["Fine wool blend", "Paisley woven border", "Comes in a gift box"],
            "s": [("Size", "100 x 200 cm"), ("Care", "Dry clean")],
        },
        "Cotton Chadar": {
            "v": [("Color", [("Off-white", "#F2EBDD"), ("Grey", "#9A9A9A"), ("Navy", "#1F2A44")])],
            "d": "A light cotton chadar for cool evenings, travel and prayer. Soft from the first wear.",
            "h": ["Light and breathable", "Fringed ends", "Machine washable"],
            "s": [("Size", "110 x 220 cm")],
        },
        "Embroidered Nagra": {
            "v": [("Color", [("Gold", "#C9A24B"), ("Black", "#151515"), ("Tan", "#B07A4A")]), ("Size", SHOE)],
            "d": "Handcrafted leather nagra with fine thread embroidery, made to match a panjabi on Eid and wedding days.",
            "h": ["Genuine leather", "Cushioned insole", "Handmade"],
            "s": [("Material", "Leather"), ("Sole", "Leather with rubber grip")],
        },
        "Brown Leather Sandal": {
            "v": [("Color", [("Brown", "#7B4A2A"), ("Black", "#151515")]), ("Size", SHOE)],
            "d": "An everyday leather sandal with a cushioned footbed and a grippy rubber sole for wet streets.",
            "h": ["Genuine leather straps", "Cushioned footbed", "Anti-slip sole"],
            "s": [("Material", "Leather, rubber")],
        },
    },
}
