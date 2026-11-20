"""The 10 SoftuneBD demo stores, defined once.

Everything else is generated from this file, so names and image filenames
always line up:
  - make_prompts.py  -> docs/demo-stores/<slug>.md, the ChatGPT image prompts
  - check_assets.py  -> reports missing / wrong-size images in demo-assets/
  - upload_assets.py -> uploads demo-assets/ to Cloudinary, writes urls.json
  - seed.py          -> builds and publishes each store from urls.json

Three themes, ten niches:
  aurora (theme 1, editorial)      noor, swarna, oudh
  bazaar (theme 2, marketplace)    voltix, freshkart, tinytots, ghorkona
  fashion-classic (theme 3, boutique)  pawpal, raaj, stride

Image sizes are what ChatGPT generates natively (1024x1024, 1536x1024,
1024x1536). The seed crops desktop heroes to 16:9 and category banners to
3.2:1 itself, so prompts ask to keep key content in the middle band.
"""

import re

# ---- image kinds: (width, height, what the image is for) ----------------
SIZES = {
    "logo": (1024, 1024, "logo mark + name on a transparent background"),
    "hero": (1536, 1024, "desktop hero banner, crops to 16:9"),
    "hero_mobile": (1024, 1024, "mobile hero banner, square"),
    "category": (1024, 1024, "category tile, square"),
    "product_square": (1024, 1024, "product photo, square"),
    "product_portrait": (1024, 1536, "product photo, portrait 2:3"),
    "event": (1536, 1024, "sale banner, also the popup"),
    "why": (1024, 1536, "why-choose-us photo, portrait"),
    "about": (1536, 1024, "about page photo, landscape"),
}

# Product photos: marketplace theme uses square cards, the two fashion-led
# themes use tall cards.
PRODUCT_KIND = {"aurora": "product_portrait", "bazaar": "product_square", "fashion-classic": "product_portrait"}

STORES = [
    # ======================= THEME 1: aurora =============================
    {
        "slug": "noor", "name": "Noor Modest", "theme": "aurora",
        "niche": "Modest fashion: abayas, hijabs and kaftans",
        "tagline": "Modesty, made beautiful",
        "colors": {"primary": "#1F1A17", "accent": "#B08D57", "surface": "#F6F1EA"},
        "fonts": {"display": "prata", "body": "work-sans"},
        "style": "Quiet luxury editorial fashion photography. Warm sand, ivory and deep charcoal palette, soft natural window light, clean minimal sets with linen and travertine, elegant South Asian women models in modest wear (hijab, abaya), calm confident poses, no logos except our own.",
        "hero": [
            "Model in a flowing sand-colored abaya and ivory hijab walking through a sunlit minimalist stone courtyard. Large serif headline on the left: \"Modesty, Made Beautiful\", small line under it: \"Eid Collection 2026\". Plenty of calm space.",
            "Three women in coordinated charcoal, olive and ivory kaftans seated on a low linen sofa, soft light. Headline: \"New Kaftan Edit\", small line: \"Up to 20% off this week\".",
        ],
        "categories": [
            ("abayas", "Abayas", "Flowing everyday and occasion abayas"),
            ("hijabs", "Hijabs", "Jersey, chiffon and silk hijabs"),
            ("kaftans", "Kaftans", "Easy, elegant kaftans"),
            ("co-ords", "Co-ord Sets", "Matching modest two-piece sets"),
            ("prayer", "Prayer Sets", "Soft prayer dresses with pouch"),
            ("accessories", "Accessories", "Pins, underscarves and bags"),
        ],
        "products": [
            ("abayas", "Sand Pleated Abaya", 4890, 5590, "Lightweight crepe abaya with soft pleats and wide sleeves."),
            ("abayas", "Midnight Open Abaya", 5290, None, "Front-open abaya in deep charcoal with a satin trim."),
            ("hijabs", "Ivory Chiffon Hijab", 890, 1090, "Breathable pleat-free chiffon, 180 x 70 cm."),
            ("hijabs", "Mocha Jersey Hijab", 790, None, "Stretchy jersey that stays in place all day."),
            ("kaftans", "Olive Linen Kaftan", 3990, 4590, "Relaxed linen kaftan with a tie waist."),
            ("kaftans", "Rose Silk Kaftan", 6490, 7290, "Fluid silk-feel kaftan for evenings and Eid."),
            ("co-ords", "Charcoal Tunic Co-ord", 4590, None, "Long tunic with wide-leg trousers."),
            ("co-ords", "Sage Wrap Co-ord", 4290, 4890, "Wrap top and pleated skirt in soft sage."),
            ("prayer", "Pearl Prayer Dress", 2490, 2890, "One-piece prayer dress with a matching pouch."),
            ("prayer", "Travel Prayer Set", 1990, None, "Folds into its own pouch, light as a scarf."),
            ("accessories", "Pearl Hijab Pins (Set of 6)", 490, 650, "Gold-tone pins with pearl heads."),
            ("accessories", "Tan Leather Tote", 3290, 3790, "Structured tote that fits an abaya and essentials."),
        ],
        "events": [
            ("Eid Edit", 20, "Eid collection banner: two women in elegant ivory and gold abayas, crescent moon motif, warm lanterns. Text: \"Eid Edit, 20% Off\"", True),
            ("Hijab Week", 15, "Flat lay of folded chiffon hijabs in sand, ivory and mocha on travertine with pearl pins. Text: \"Hijab Week, 15% Off\"", False),
        ],
        "why": "Close-up of hands adjusting an ivory hijab with a pearl pin, soft window light, linen backdrop.",
        "about": "A calm boutique studio with abayas on a wooden rail, linen curtains, a woman folding a hijab at a table.",
        "texts": {
            "why": [("Fabrics that breathe", "Crepe, linen and chiffon chosen for Bangladesh heat, comfortable from morning to night."),
                    ("Cut for real life", "Generous lengths, easy sleeves and pockets where you need them."),
                    ("Designed in Dhaka", "Every piece is designed and finished by our own team.")],
            "cta": ("Find your Eid look", "New abayas, kaftans and co-ords, 20% off for Eid."),
            "about": "Noor Modest makes modest wear that feels as good as it looks: soft fabrics, honest prices and designs made for everyday life in Bangladesh.",
        },
    },
    {
        "slug": "swarna", "name": "Swarna Jewels", "theme": "aurora",
        "niche": "Jewellery: gold-plated, silver and bridal sets",
        "tagline": "Jewellery for every celebration",
        "colors": {"primary": "#3A2A12", "accent": "#C9A24A", "surface": "#FBF7EF"},
        "fonts": {"display": "prata", "body": "work-sans"},
        "style": "High-end jewellery photography. Ivory, champagne gold and deep brown palette, soft spotlights and gentle reflections, macro detail, silk and marble surfaces, elegant South Asian model shots for worn pieces. Luxurious, warm, refined.",
        "hero": [
            "Close portrait of a bride wearing an ornate gold necklace and jhumka earrings, soft golden light. Serif headline: \"Made for Your Big Day\", small line: \"Bridal Collection\".",
            "Gold bangles, rings and earrings arranged on cream silk with soft shadows. Headline: \"Everyday Gold\", small line: \"Starting at Tk 990\".",
        ],
        "categories": [
            ("necklaces", "Necklaces", "Chains, chokers and statement sets"),
            ("earrings", "Earrings", "Studs, hoops and jhumkas"),
            ("bangles", "Bangles", "Classic and modern bangles"),
            ("rings", "Rings", "Everyday and occasion rings"),
            ("bridal", "Bridal Sets", "Complete sets for the wedding"),
            ("silver", "Silver", "Sterling silver pieces"),
        ],
        "products": [
            ("necklaces", "Lakshmi Gold Choker", 5490, 6290, "22k gold-plated choker with ruby-red stones."),
            ("necklaces", "Dainty Pendant Chain", 1290, None, "Fine chain with a tiny teardrop pendant."),
            ("earrings", "Temple Jhumka Earrings", 1890, 2290, "Gold-plated jhumkas with pearl drops."),
            ("earrings", "Pearl Stud Earrings", 990, None, "Freshwater-look pearls on gold-tone posts."),
            ("bangles", "Filigree Bangle Pair", 2490, 2890, "Hand-finished filigree, set of two."),
            ("bangles", "Slim Stacking Bangles (Set of 6)", 1590, None, "Thin polished bangles for stacking."),
            ("rings", "Kundan Cocktail Ring", 1490, 1790, "Adjustable ring with a kundan centre stone."),
            ("rings", "Minimal Band Ring", 790, None, "Smooth gold-plated band, everyday wear."),
            ("bridal", "Rani Bridal Set", 14990, 17990, "Necklace, earrings, tikka and nath set."),
            ("bridal", "Rose Kundan Bridal Set", 11990, 13490, "Kundan and pink stone set with earrings."),
            ("silver", "Sterling Silver Anklet", 1690, None, "925 silver anklet with tiny bells."),
            ("silver", "Silver Hoop Earrings", 1290, 1490, "925 silver hoops, 3 cm."),
        ],
        "events": [
            ("Wedding Season", 15, "Bridal jewellery banner: gold bridal set on red silk with marigold petals. Text: \"Wedding Season, 15% Off\"", True),
            ("Silver Sale", 20, "Sterling silver pieces on grey marble with soft light. Text: \"Silver Sale, 20% Off\"", False),
        ],
        "why": "Macro shot of a craftsman's hands polishing a gold bangle with a soft cloth, warm workshop light.",
        "about": "An elegant jewellery showroom with glass counters, warm lighting and velvet display busts.",
        "texts": {
            "why": [("Anti-tarnish finish", "Every gold-plated piece is coated to keep its shine for years."),
                    ("Skin-friendly", "Nickel-free and hypoallergenic, comfortable all day."),
                    ("Gift-ready box", "Each order arrives in our signature velvet box.")],
            "cta": ("Shine this wedding season", "Bridal sets and festive gold, 15% off for a limited time."),
            "about": "Swarna Jewels brings fine-looking jewellery within everyone's reach: designs inspired by Bengali tradition, finished to last.",
        },
    },
    {
        "slug": "oudh", "name": "Oudh House", "theme": "aurora",
        "niche": "Perfume, attar and home fragrance",
        "tagline": "Scents that stay",
        "colors": {"primary": "#14110F", "accent": "#C08A3E", "surface": "#F3EEE7"},
        "fonts": {"display": "prata", "body": "work-sans"},
        "style": "Luxury fragrance campaign photography. Deep black, amber and smoked-gold palette, dramatic side light, glass bottles with liquid glow, oud wood, rose petals, smoke wisps, dark stone and brass. Moody, rich, premium.",
        "hero": [
            "Elegant amber glass perfume bottle on dark stone with oud wood chips and a wisp of smoke, golden rim light. Serif headline: \"The Art of Oud\", small line: \"New Signature Collection\".",
            "Row of three attar bottles in gold and black, rose petals scattered, warm glow. Headline: \"Attar, Reimagined\", small line: \"Alcohol-free, long lasting\".",
        ],
        "categories": [
            ("perfumes", "Perfumes", "Eau de parfum for him and her"),
            ("attar", "Attar", "Alcohol-free concentrated oils"),
            ("oud", "Oud", "Rare oud oils and blends"),
            ("bakhoor", "Bakhoor", "Incense chips for the home"),
            ("gift-sets", "Gift Sets", "Boxed sets for every occasion"),
            ("home", "Home Fragrance", "Reed diffusers and room sprays"),
        ],
        "products": [
            ("perfumes", "Noir Amber EDP 100ml", 4990, 5790, "Warm amber, vanilla and smoked wood."),
            ("perfumes", "Rose Royale EDP 100ml", 4590, None, "Damask rose with saffron and musk."),
            ("attar", "White Musk Attar 12ml", 890, 1090, "Clean, soft musk, alcohol-free."),
            ("attar", "Majmua Attar 12ml", 990, None, "Classic blend of khus, rose and sandal."),
            ("oud", "Cambodian Oud Oil 3ml", 3490, 3990, "Deep, smoky pure oud oil."),
            ("oud", "Oud Wood Blend 12ml", 1890, None, "Oud softened with sandalwood."),
            ("bakhoor", "Royal Bakhoor 50g", 1290, 1490, "Sweet, smoky bakhoor chips."),
            ("bakhoor", "Brass Bakhoor Burner", 2190, None, "Hand-finished brass mabkhara."),
            ("gift-sets", "Discovery Set (6 x 3ml)", 1990, 2390, "Try six signature attars."),
            ("gift-sets", "His & Hers Gift Box", 7990, 8990, "Two 50ml perfumes in a gift box."),
            ("home", "Amber Reed Diffuser", 2490, None, "Lasts up to 8 weeks."),
            ("home", "Oud Room Spray", 1290, 1490, "Instant oud fragrance for any room."),
        ],
        "events": [
            ("Signature Week", 15, "Dark luxury banner with a perfume bottle and golden smoke. Text: \"Signature Week, 15% Off\"", True),
            ("Gift Sets Sale", 10, "Elegant black gift boxes with gold ribbon and small bottles. Text: \"Gift Sets, 10% Off\"", False),
        ],
        "why": "Close-up of a glass dropper releasing golden attar oil, dark background, warm rim light.",
        "about": "A dark wood perfumer's studio with shelves of glass bottles and a brass scale, warm lamp light.",
        "texts": {
            "why": [("Long lasting", "Concentrated blends that stay 8 to 12 hours on skin."),
                    ("Alcohol-free attars", "Pure oil attars, gentle on skin and suitable for prayer."),
                    ("Test before you buy", "Discovery sets let you find your scent first.")],
            "cta": ("Find your signature scent", "Perfumes, attars and oud, 15% off this week."),
            "about": "Oudh House blends traditional attar craft with modern perfumery, so everyone can find a scent that feels like theirs.",
        },
    },
    # ======================= THEME 2: bazaar =============================
    {
        "slug": "voltix", "name": "Voltix", "theme": "bazaar",
        "niche": "Electronics and gadgets",
        "tagline": "Gadgets you'll love",
        "colors": {"primary": "#2563EB", "accent": "#0F172A", "surface": "#F5F7FB"},
        "fonts": {"display": "plus-jakarta-sans", "body": "inter"},
        "style": "Clean modern tech product photography. Cool white, electric blue and deep navy palette, studio lighting with soft reflections, floating products, subtle gradients, crisp and premium. Product designs are our own unbranded Voltix models (no Apple, Samsung, JBL or other real brand logos).",
        "hero": [
            "Wireless headphones, smartwatch and earbuds floating over an electric blue gradient with soft glow. Bold headline: \"Big Tech Sale\", line under: \"Up to 15% off top gadgets\", small \"Voltix\" wordmark.",
            "Sleek laptop and phone on a navy desk with blue light accents. Headline: \"Work. Play. Create.\", line: \"Laptops from Tk 52,990\".",
        ],
        "categories": [
            ("headphones", "Headphones", "Wireless and noise cancelling"),
            ("smartwatches", "Smartwatches", "Fitness and calls on your wrist"),
            ("phones", "Smartphones", "Phones for every budget"),
            ("laptops", "Laptops", "Work, study and gaming"),
            ("speakers", "Speakers", "Portable Bluetooth speakers"),
            ("power", "Power & Charging", "Power banks and chargers"),
        ],
        "products": [
            ("headphones", "Voltix Aura ANC Headphones", 8990, 10990, "Active noise cancelling, 40-hour battery."),
            ("headphones", "Voltix Pods Pro Earbuds", 3990, 4990, "ANC earbuds with a wireless charging case."),
            ("smartwatches", "Voltix Watch S2 AMOLED", 6490, 7990, "AMOLED display, Bluetooth calls, SpO2."),
            ("smartwatches", "Voltix Watch Active", 3990, None, "100+ sport modes, 10-day battery."),
            ("phones", "Voltix Nova 12 (8/256GB)", 32990, 35990, "120Hz display, 50MP camera, 5000mAh."),
            ("phones", "Voltix Nova Lite (6/128GB)", 18990, None, "Big battery and a smooth 90Hz screen."),
            ("laptops", "Voltix Book Air 14", 74990, 79990, "Thin and light, 16GB RAM, 512GB SSD."),
            ("laptops", "Voltix Book Pro 16", 124990, None, "Creator laptop with fast graphics."),
            ("speakers", "Voltix Boom Go Speaker", 3490, 3990, "IP67 waterproof, punchy 20W sound."),
            ("speakers", "Voltix Party Max Speaker", 9990, 11990, "60W party speaker with lights."),
            ("power", "Voltix PowerCell 20000mAh", 2490, 2990, "22.5W fast charging, three outputs."),
            ("power", "Voltix 65W GaN Charger", 2290, None, "Charges laptop and phone together."),
        ],
        "events": [
            ("11.11 Tech Sale", 15, "Electric blue sale banner with floating headphones, watch and speaker, light streaks. Text: \"11.11 Tech Sale, 15% Off\"", True),
            ("Power Week", 10, "Power banks and chargers on a navy background with glowing cables. Text: \"Power Week, 10% Off\"", False),
        ],
        "why": "Technician's hands testing a smartphone at a clean white workbench with tools, cool light.",
        "about": "A modern electronics showroom with lit shelves of gadgets and a friendly staff counter.",
        "texts": {
            "why": [("Genuine products", "Every gadget comes with an official warranty card."),
                    ("Tested before shipping", "We check each device so it works out of the box."),
                    ("Real support", "WhatsApp help every day from 10 AM to 10 PM.")],
            "cta": ("The Tech Sale is on", "Up to 15% off headphones, watches and speakers."),
            "about": "Voltix brings genuine gadgets to Bangladesh with official warranty, honest prices and cash on delivery nationwide.",
        },
    },
    {
        "slug": "freshkart", "name": "FreshKart", "theme": "bazaar",
        "niche": "Grocery: fresh produce, pantry and daily essentials",
        "tagline": "Fresh to your door",
        "colors": {"primary": "#16A34A", "accent": "#14532D", "surface": "#F4FAF3"},
        "fonts": {"display": "plus-jakarta-sans", "body": "inter"},
        "style": "Bright fresh food photography. Leafy green, sunny yellow and clean white palette, natural daylight, water droplets on produce, wooden crates and kraft paper bags, appetising and cheerful. Packaging is our own unbranded FreshKart design.",
        "hero": [
            "Overflowing kraft grocery bag with fresh vegetables, fruits and greens on a bright green background. Bold headline: \"Fresh Groceries in 1 Hour\", line: \"Free delivery over Tk 999\".",
            "Colourful fruits arranged in a wooden crate with droplets, sunny light. Headline: \"Farm Fresh Fruits\", line: \"Up to 20% off this week\".",
        ],
        "categories": [
            ("vegetables", "Vegetables", "Fresh local vegetables daily"),
            ("fruits", "Fruits", "Seasonal and imported fruits"),
            ("rice-dal", "Rice & Dal", "Rice, lentils and flour"),
            ("dairy", "Dairy & Eggs", "Milk, yoghurt, butter and eggs"),
            ("snacks", "Snacks", "Biscuits, chips and more"),
            ("cleaning", "Cleaning", "Detergent and household care"),
        ],
        "products": [
            ("vegetables", "Fresh Tomatoes 1kg", 120, 150, "Ripe red tomatoes, locally grown."),
            ("vegetables", "Green Broccoli 500g", 180, None, "Crisp green florets."),
            ("fruits", "Fuji Apples 1kg", 390, 450, "Sweet, crunchy imported apples."),
            ("fruits", "Sagar Bananas (12 pcs)", 140, None, "Naturally ripened local bananas."),
            ("rice-dal", "Premium Miniket Rice 5kg", 420, 470, "Fine, fragrant everyday rice."),
            ("rice-dal", "Red Lentils (Masoor) 1kg", 145, None, "Clean, quick-cooking lentils."),
            ("dairy", "Fresh Milk 1L", 95, None, "Pasteurised full-cream milk."),
            ("dairy", "Farm Eggs (12 pcs)", 165, 180, "Fresh brown eggs."),
            ("snacks", "Butter Cookies 400g", 260, 300, "Crisp butter cookies in a tin."),
            ("snacks", "Salted Potato Chips 150g", 90, None, "Thin, crunchy salted chips."),
            ("cleaning", "Laundry Powder 2kg", 340, 380, "Tough on stains, gentle on clothes."),
            ("cleaning", "Dishwash Liquid 500ml", 120, None, "Lemon fresh, cuts grease fast."),
        ],
        "events": [
            ("Weekend Fresh Deals", 20, "Bright banner with vegetables and fruits bursting out of a basket on green. Text: \"Weekend Fresh Deals, 20% Off\"", True),
            ("Pantry Restock", 10, "Neat pantry shelf with rice, lentils and jars in kraft packaging. Text: \"Pantry Restock, 10% Off\"", False),
        ],
        "why": "Smiling farmer holding a crate of fresh vegetables in a green field, morning sun.",
        "about": "A clean, bright grocery warehouse with neatly packed orders in kraft bags ready for delivery.",
        "texts": {
            "why": [("Fresh every morning", "Produce sourced from farms the same day."),
                    ("Fast delivery", "Inside Dhaka within one to three hours."),
                    ("Fair prices", "Market prices, no hidden charges.")],
            "cta": ("Fill your fridge for less", "Fresh fruits and vegetables up to 20% off this weekend."),
            "about": "FreshKart delivers fresh groceries and daily essentials across Dhaka, sourced straight from farms and trusted suppliers.",
        },
    },
    {
        "slug": "tinytots", "name": "TinyTots", "theme": "bazaar",
        "niche": "Kids: toys, baby care and kids' clothing",
        "tagline": "Little things, big smiles",
        "colors": {"primary": "#F97316", "accent": "#7C3AED", "surface": "#FFF8F1"},
        "fonts": {"display": "plus-jakarta-sans", "body": "inter"},
        "style": "Playful bright kids' product photography. Soft pastel orange, lilac, mint and sky blue palette, rounded shapes, wooden toys, soft daylight, cheerful and safe-feeling. Toys are our own unbranded designs; children shown are happy and fully clothed.",
        "hero": [
            "Smiling toddler playing with colourful wooden blocks on a soft pastel rug. Rounded bold headline: \"Play. Learn. Grow.\", line: \"New toys every week\".",
            "Pastel baby care essentials and plush toys arranged playfully on a lilac background. Headline: \"Baby Care Week\", line: \"Up to 15% off\".",
        ],
        "categories": [
            ("toys", "Toys", "Wooden and educational toys"),
            ("plush", "Plush Toys", "Soft toys and cuddly friends"),
            ("baby-care", "Baby Care", "Gentle care for little ones"),
            ("feeding", "Feeding", "Bottles, bibs and bowls"),
            ("kids-wear", "Kids Wear", "Comfy everyday clothes"),
            ("school", "School", "Bags, bottles and stationery"),
        ],
        "products": [
            ("toys", "Rainbow Stacking Blocks", 1290, 1490, "20 smooth wooden blocks in pastel colours."),
            ("toys", "Shape Sorter Cube", 890, None, "Wooden cube with 12 shapes to sort."),
            ("plush", "Cuddly Bunny Plush 30cm", 790, 950, "Super-soft bunny, safe for babies."),
            ("plush", "Sleepy Bear Plush", 990, None, "Soft bear with a sleepy smile."),
            ("baby-care", "Gentle Baby Wash 400ml", 590, 690, "Tear-free, mild baby wash."),
            ("baby-care", "Baby Lotion 300ml", 520, None, "Light, fragrance-free moisture."),
            ("feeding", "Anti-colic Bottle 250ml", 690, 790, "Soft nipple, anti-colic vent."),
            ("feeding", "Silicone Bib Set (2 pcs)", 450, None, "Wipe-clean bibs with a food catcher."),
            ("kids-wear", "Cotton Romper (2 pack)", 990, 1190, "Soft cotton rompers with snaps."),
            ("kids-wear", "Dino Print T-shirt", 490, None, "Breathable cotton tee with dinosaurs."),
            ("school", "Kids Backpack Unicorn", 1290, 1490, "Light backpack with padded straps."),
            ("school", "Steel Water Bottle 400ml", 690, None, "Leak-proof, keeps drinks cool."),
        ],
        "events": [
            ("Toy Fest", 15, "Playful banner with wooden toys, balloons and confetti on pastel orange. Text: \"Toy Fest, 15% Off\"", True),
            ("Back to School", 10, "Kids backpacks, bottles and stationery on a mint background. Text: \"Back to School, 10% Off\"", False),
        ],
        "why": "Mother and child playing with wooden toys on a soft rug in a bright, cosy room.",
        "about": "A cheerful kids' store with neat shelves of toys and pastel decor.",
        "texts": {
            "why": [("Safe materials", "Non-toxic paints and baby-safe fabrics."),
                    ("Learning through play", "Toys picked to build skills and imagination."),
                    ("Easy returns", "7-day returns on unopened items.")],
            "cta": ("Toy Fest is here", "Wooden toys, plush and more, 15% off."),
            "about": "TinyTots brings safe, joyful toys and baby essentials to families across Bangladesh.",
        },
    },
    {
        "slug": "ghorkona", "name": "Ghorkona", "theme": "bazaar",
        "niche": "Home decor, kitchen and dining",
        "tagline": "Make your home yours",
        "colors": {"primary": "#B45309", "accent": "#1C1917", "surface": "#FAF6F0"},
        "fonts": {"display": "plus-jakarta-sans", "body": "inter"},
        "style": "Warm cosy interior and homeware photography. Terracotta, cream, warm wood and sage palette, soft afternoon light, styled tabletops and living rooms, handmade textures like jute, clay and cotton. Inviting and calm.",
        "hero": [
            "Cosy living room corner with jute rug, terracotta vases, cushions and warm lamp light. Bold headline: \"Make Your Home Yours\", line: \"New decor collection\".",
            "Beautifully set dining table with ceramic plates, brass cutlery and linen. Headline: \"Dine in Style\", line: \"Up to 20% off dinnerware\".",
        ],
        "categories": [
            ("decor", "Decor", "Vases, frames and accents"),
            ("cushions", "Cushions & Throws", "Soft textures for every room"),
            ("lighting", "Lighting", "Lamps and string lights"),
            ("dinnerware", "Dinnerware", "Plates, bowls and mugs"),
            ("kitchen", "Kitchen", "Cookware and storage"),
            ("plants", "Plants & Pots", "Planters and faux greenery"),
        ],
        "products": [
            ("decor", "Terracotta Bud Vase Set", 1490, 1790, "Three handmade clay vases."),
            ("decor", "Brass Photo Frame 5x7", 990, None, "Brushed brass frame with stand."),
            ("cushions", "Jute Cushion Cover 18in", 590, 690, "Natural jute with cotton back."),
            ("cushions", "Knitted Throw Blanket", 2490, None, "Chunky knit throw in cream."),
            ("lighting", "Rattan Table Lamp", 3290, 3790, "Woven rattan shade, warm light."),
            ("lighting", "Warm Fairy Lights 10m", 690, None, "100 warm white LEDs, USB powered."),
            ("dinnerware", "Stoneware Dinner Set (16 pcs)", 5990, 6990, "Speckled cream stoneware for four."),
            ("dinnerware", "Handpainted Mug Pair", 890, None, "Two ceramic mugs with leaf motifs."),
            ("kitchen", "Non-stick Fry Pan 26cm", 1690, 1990, "Granite-coated, induction ready."),
            ("kitchen", "Glass Storage Jars (Set of 3)", 990, None, "Airtight jars with bamboo lids."),
            ("plants", "Ceramic Planter Duo", 1290, 1490, "Two matte planters with saucers."),
            ("plants", "Faux Olive Tree 120cm", 4490, None, "Lifelike olive tree in a pot."),
        ],
        "events": [
            ("Home Refresh Sale", 20, "Warm banner of a styled living room with terracotta and cream decor. Text: \"Home Refresh, 20% Off\"", True),
            ("Kitchen Week", 10, "Cookware and ceramic dinnerware on a wooden counter with herbs. Text: \"Kitchen Week, 10% Off\"", False),
        ],
        "why": "Artisan's hands shaping a clay vase on a pottery wheel, warm light.",
        "about": "A bright homeware store with styled shelves of ceramics, cushions and plants.",
        "texts": {
            "why": [("Handmade touches", "Many pieces are made by local artisans."),
                    ("Packed with care", "Fragile items are double-wrapped for safe delivery."),
                    ("Style help", "Ask us on WhatsApp for decor ideas, free.")],
            "cta": ("Refresh your home", "Decor, lighting and dinnerware up to 20% off."),
            "about": "Ghorkona curates warm, handmade-feel decor and kitchenware so every home in Bangladesh can feel personal.",
        },
    },
    # ===================== THEME 3: fashion-classic ======================
    {
        "slug": "pawpal", "name": "PawPal", "theme": "fashion-classic",
        "niche": "Pet supplies for cats and dogs",
        "tagline": "Everything your pet loves",
        "colors": {"primary": "#0F766E", "accent": "#F59E0B", "surface": "#FFF9F0"},
        "fonts": {"display": "prata", "body": "work-sans"},
        "style": "Warm, happy pet lifestyle photography. Teal, warm yellow and cream palette, soft daylight, cute healthy cats and dogs, cosy homes, clean product shots of pet food, toys and beds. Packaging is our own unbranded PawPal design.",
        "hero": [
            "Happy golden retriever and a tabby cat sitting together on a cosy cream sofa. Headline: \"Everything Your Pet Loves\", line: \"Free delivery over Tk 2,000\".",
            "Cute kitten playing with a feather toy on a teal background. Headline: \"New Toys & Treats\", line: \"Up to 15% off\".",
        ],
        "categories": [
            ("cat-food", "Cat Food", "Dry and wet food for cats"),
            ("dog-food", "Dog Food", "Nutritious food for dogs"),
            ("toys", "Toys", "Play time favourites"),
            ("beds", "Beds", "Cosy beds and mats"),
            ("grooming", "Grooming", "Shampoo, brushes and care"),
            ("accessories", "Accessories", "Collars, leashes and bowls"),
        ],
        "products": [
            ("cat-food", "Ocean Fish Cat Food 1.5kg", 1390, 1590, "Complete dry food with omega-3."),
            ("cat-food", "Chicken Gravy Pouches (12)", 1090, None, "Tender chicken in gravy."),
            ("dog-food", "Chicken & Rice Dog Food 3kg", 2490, 2790, "Balanced adult dog food."),
            ("dog-food", "Puppy Starter 1.5kg", 1590, None, "Small kibble for growing puppies."),
            ("toys", "Feather Wand Cat Toy", 390, 490, "Interactive wand with feathers and bell."),
            ("toys", "Rope Tug Dog Toy", 450, None, "Tough cotton rope for tug and chew."),
            ("beds", "Donut Plush Pet Bed", 1890, 2290, "Calming round bed, washable."),
            ("beds", "Cooling Pet Mat", 1290, None, "Gel mat to keep pets cool in summer."),
            ("grooming", "Oatmeal Pet Shampoo 300ml", 590, 690, "Gentle on sensitive skin."),
            ("grooming", "Slicker Brush", 490, None, "Removes loose fur and tangles."),
            ("accessories", "Padded Nylon Collar", 390, None, "Adjustable collar with a soft lining."),
            ("accessories", "Steel Bowl Pair", 590, 690, "Non-slip stainless steel bowls."),
        ],
        "events": [
            ("Pet Care Week", 15, "Banner with a happy dog and cat surrounded by toys and treats on teal. Text: \"Pet Care Week, 15% Off\"", True),
            ("Food Stock-up", 10, "Bags of pet food and bowls on a clean background. Text: \"Stock Up, 10% Off\"", False),
        ],
        "why": "Veterinarian gently checking a calm puppy on a clean table, friendly atmosphere.",
        "about": "A friendly pet store with neat shelves of food and toys, a dog waiting happily at the counter.",
        "texts": {
            "why": [("Vet-approved food", "Recipes chosen with local vets."),
                    ("Fast delivery", "Pet food at your door, inside Dhaka in 24 hours."),
                    ("Pet-friendly prices", "Bulk savings on everyday essentials.")],
            "cta": ("Spoil your best friend", "Toys, treats and beds, 15% off this week."),
            "about": "PawPal is for people who love their pets: quality food, toys and care, delivered across Bangladesh.",
        },
    },
    {
        "slug": "raaj", "name": "Raaj", "theme": "fashion-classic",
        "niche": "Men's panjabi, ethnic and festive wear",
        "tagline": "Wear your heritage",
        "colors": {"primary": "#7F1D1D", "accent": "#C8A15A", "surface": "#F8F4EE"},
        "fonts": {"display": "prata", "body": "work-sans"},
        "style": "Premium menswear editorial photography. Deep maroon, ivory, gold and charcoal palette, warm golden-hour light, heritage settings like old brick walls, wooden doors and courtyards, confident South Asian male models in panjabis and koties.",
        "hero": [
            "Confident man in an ivory embroidered panjabi with a maroon koti in front of a heritage brick wall, golden light. Serif headline: \"Wear Your Heritage\", line: \"Eid Panjabi Collection\".",
            "Two men in coordinated festive panjabis laughing in a sunlit courtyard. Headline: \"Festive Edit\", line: \"Up to 20% off\".",
        ],
        "categories": [
            ("panjabi", "Panjabi", "Everyday and festive panjabis"),
            ("koti", "Koti", "Waistcoats to layer"),
            ("pajama", "Pajama", "Churidar and straight pajamas"),
            ("fatua", "Fatua", "Short kurtas for casual days"),
            ("shawls", "Shawls", "Winter shawls and wraps"),
            ("footwear", "Footwear", "Nagra and leather sandals"),
        ],
        "products": [
            ("panjabi", "Ivory Embroidered Panjabi", 3490, 3990, "Cotton-silk panjabi with neck embroidery."),
            ("panjabi", "Maroon Jamdani Panjabi", 4290, None, "Jamdani-woven panjabi for Eid."),
            ("koti", "Gold Brocade Koti", 2690, 2990, "Brocade waistcoat with fabric buttons."),
            ("koti", "Charcoal Linen Koti", 2190, None, "Light linen koti for day events."),
            ("pajama", "White Churidar Pajama", 890, None, "Soft cotton churidar."),
            ("pajama", "Beige Straight Pajama", 950, 1090, "Comfortable straight cut."),
            ("fatua", "Indigo Block Print Fatua", 1290, 1490, "Hand block-printed cotton."),
            ("fatua", "Olive Khadi Fatua", 1190, None, "Breathable khadi cotton."),
            ("shawls", "Kashmiri Wool Shawl", 3990, 4590, "Soft wool with paisley border."),
            ("shawls", "Cotton Chadar", 1490, None, "Light cotton wrap for winter evenings."),
            ("footwear", "Embroidered Nagra", 2190, 2490, "Handcrafted leather nagra."),
            ("footwear", "Brown Leather Sandal", 1890, None, "Cushioned everyday sandal."),
        ],
        "events": [
            ("Eid Panjabi Fest", 20, "Festive banner: man in rich maroon and gold panjabi, lanterns and crescent moon. Text: \"Eid Panjabi Fest, 20% Off\"", True),
            ("Winter Shawls", 15, "Folded wool shawls in earthy tones on a wooden chair. Text: \"Winter Shawls, 15% Off\"", False),
        ],
        "why": "Close-up of a tailor's hands hand-embroidering gold thread on ivory fabric.",
        "about": "A heritage-style menswear boutique with panjabis on wooden racks and warm lights.",
        "texts": {
            "why": [("Handpicked fabrics", "Cotton-silk, jamdani and khadi from local weavers."),
                    ("Tailored fit", "Cut for a comfortable, sharp fit."),
                    ("Easy exchange", "Size not right? Exchange within 7 days.")],
            "cta": ("Get ready for Eid", "New panjabis and koties, 20% off."),
            "about": "Raaj celebrates Bengali menswear with panjabis and festive wear made from fabrics woven in Bangladesh.",
        },
    },
    {
        "slug": "stride", "name": "Stride", "theme": "fashion-classic",
        "niche": "Footwear: sneakers, formal shoes and sandals",
        "tagline": "Every step, in style",
        "colors": {"primary": "#111827", "accent": "#EF4444", "surface": "#F5F5F4"},
        "fonts": {"display": "prata", "body": "work-sans"},
        "style": "Bold modern footwear photography. Off-white, charcoal and a punchy red accent, studio light with crisp shadows, shoes floating or on concrete plinths, urban lifestyle shots. Shoe designs are our own unbranded Stride models (no Nike, Adidas or other logos).",
        "hero": [
            "Pair of white and red sneakers floating mid-air over a clean off-white background with motion shadow. Headline: \"Every Step, In Style\", line: \"New Sneaker Drop\".",
            "Person walking on a city crosswalk in charcoal sneakers, low angle, sunlight. Headline: \"Made to Move\", line: \"Up to 20% off\".",
        ],
        "categories": [
            ("sneakers", "Sneakers", "Everyday and running sneakers"),
            ("formal", "Formal Shoes", "Leather oxfords and loafers"),
            ("sandals", "Sandals", "Comfort sandals and slides"),
            ("women", "Women's", "Flats, heels and sneakers"),
            ("kids", "Kids", "Light shoes for little feet"),
            ("care", "Shoe Care", "Cleaners, polish and socks"),
        ],
        "products": [
            ("sneakers", "Stride Aero Runner", 3990, 4590, "Lightweight knit runner with foam sole."),
            ("sneakers", "Stride Court Classic", 3490, None, "White leather court sneaker."),
            ("formal", "Oxford Leather Shoe", 4890, 5490, "Polished leather oxford, cushioned insole."),
            ("formal", "Suede Penny Loafer", 4290, None, "Soft suede loafer for smart days."),
            ("sandals", "Cloud Comfort Slide", 1290, 1490, "Soft slide with a contoured footbed."),
            ("sandals", "Leather Strap Sandal", 1990, None, "Adjustable strap sandal."),
            ("women", "Block Heel Mule", 2690, 2990, "Comfortable block heel in blush."),
            ("women", "Ballet Flat", 1890, None, "Soft leather ballet flat."),
            ("kids", "Kids Light-Up Sneaker", 1790, 1990, "Fun lights, easy hook-and-loop strap."),
            ("kids", "Kids School Shoe", 1590, None, "Durable black school shoe."),
            ("care", "Sneaker Cleaning Kit", 690, 790, "Foam cleaner, brush and cloth."),
            ("care", "Cotton Ankle Socks (5 pairs)", 590, None, "Breathable cotton socks."),
        ],
        "events": [
            ("Sneaker Drop", 15, "Bold banner with floating sneakers in red and white with motion lines. Text: \"Sneaker Drop, 15% Off\"", True),
            ("Office Ready", 10, "Leather oxfords and loafers on concrete plinths, soft shadows. Text: \"Office Ready, 10% Off\"", False),
        ],
        "why": "Close-up of a shoemaker stitching leather by hand in a workshop, warm light.",
        "about": "A modern shoe store with lit display walls of sneakers and a seating area.",
        "texts": {
            "why": [("All-day comfort", "Cushioned soles designed for long Dhaka days."),
                    ("Durable build", "Quality materials that last."),
                    ("Easy size exchange", "Free size exchange within 7 days.")],
            "cta": ("The Sneaker Drop is live", "New sneakers and slides, 15% off."),
            "about": "Stride makes comfortable, good-looking footwear for every step of the day, at prices that make sense.",
        },
    },
]


def image_list(store: dict) -> list[tuple[str, str, str]]:
    """Every image a store needs: (filename, size kind, prompt). The order is
    the order the prompts are written in."""
    out = [("logo.png", "logo", f"Logo for \"{store['name']}\", a brand selling {store['niche'].lower()}: a simple, memorable mark next to the name \"{store['name']}\" in a refined font, in the brand colours. Transparent background, centred, no mockup.")]
    for i, h in enumerate(store["hero"], 1):
        out.append((f"hero-{i}.png", "hero", h + " Keep all text and key subjects inside the middle 80% of the height (it will be cropped to 16:9)."))
        out.append((f"hero-{i}-mobile.png", "hero_mobile", "Square mobile version of the previous banner: same scene, subject centred, the same text stacked and large enough to read on a phone."))
    for slug, name, desc in store["categories"]:
        out.append((f"category-{slug}.png", "category", f"Category image for \"{name}\" ({desc.lower()}). One clear hero subject, plenty of space, no text."))
    kind = PRODUCT_KIND[store["theme"]]
    for i, (_cat, name, _p, _c, short) in enumerate(store["products"], 1):
        out.append((f"product-{i:02d}.png", kind, f"Product photo: {name}. {short} Studio shot on a clean background in the brand palette, the product fills most of the frame, no text, no watermark."))
    for i, (name, _d, prompt, _popup) in enumerate(store["events"], 1):
        # No text in event images: the theme prints the event name and
        # discount itself, so baked-in text would show twice.
        scene = re.sub(r'\.?\s*Text:\s*"[^"]*"', "", prompt).strip().rstrip(".")
        out.append((f"event-{i}.png", "event", scene + ". Image only: no text, no numbers, no logos anywhere. Keep the main subject in the centre."))
    out.append(("why.png", "why", store["why"] + " No text."))
    out.append(("about.png", "about", store["about"] + " No text."))
    return out
