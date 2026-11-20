# Demo store image prompts

10 demo stores built on SoftuneBD's 3 themes. For each store: open a new ChatGPT chat,
paste section 1 of its file, then ask for each image in section 2 and save it with the exact
filename into `demo-assets/<store>/` (that folder is not in git).

When a store's folder is complete, check it:

```
venv\Scripts\python.exe scripts\demo_stores\check_assets.py
```

| Store | Theme | Niche | Images | One-paste prompt (step by step) | Run-all prompt (saves files itself) | Details |
|---|---|---|---|---|---|---|
| Noor Modest | Theme 1 (editorial) | Modest fashion: abayas, hijabs and kaftans | 27 | [noor-paste-this.txt](noor-paste-this.txt) | [noor-run-all.txt](noor-run-all.txt) | [noor.md](noor.md) |
| Swarna Jewels | Theme 1 (editorial) | Jewellery: gold-plated, silver and bridal sets | 27 | [swarna-paste-this.txt](swarna-paste-this.txt) | [swarna-run-all.txt](swarna-run-all.txt) | [swarna.md](swarna.md) |
| Oudh House | Theme 1 (editorial) | Perfume, attar and home fragrance | 27 | [oudh-paste-this.txt](oudh-paste-this.txt) | [oudh-run-all.txt](oudh-run-all.txt) | [oudh.md](oudh.md) |
| Voltix | Theme 2 (marketplace) | Electronics and gadgets | 27 | [voltix-paste-this.txt](voltix-paste-this.txt) | [voltix-run-all.txt](voltix-run-all.txt) | [voltix.md](voltix.md) |
| FreshKart | Theme 2 (marketplace) | Grocery: fresh produce, pantry and daily essentials | 27 | [freshkart-paste-this.txt](freshkart-paste-this.txt) | [freshkart-run-all.txt](freshkart-run-all.txt) | [freshkart.md](freshkart.md) |
| TinyTots | Theme 2 (marketplace) | Kids: toys, baby care and kids' clothing | 27 | [tinytots-paste-this.txt](tinytots-paste-this.txt) | [tinytots-run-all.txt](tinytots-run-all.txt) | [tinytots.md](tinytots.md) |
| Ghorkona | Theme 2 (marketplace) | Home decor, kitchen and dining | 27 | [ghorkona-paste-this.txt](ghorkona-paste-this.txt) | [ghorkona-run-all.txt](ghorkona-run-all.txt) | [ghorkona.md](ghorkona.md) |
| PawPal | Theme 3 (boutique) | Pet supplies for cats and dogs | 27 | [pawpal-paste-this.txt](pawpal-paste-this.txt) | [pawpal-run-all.txt](pawpal-run-all.txt) | [pawpal.md](pawpal.md) |
| Raaj | Theme 3 (boutique) | Men's panjabi, ethnic and festive wear | 27 | [raaj-paste-this.txt](raaj-paste-this.txt) | [raaj-run-all.txt](raaj-run-all.txt) | [raaj.md](raaj.md) |
| Stride | Theme 3 (boutique) | Footwear: sneakers, formal shoes and sandals | 27 | [stride-paste-this.txt](stride-paste-this.txt) | [stride-run-all.txt](stride-run-all.txt) | [stride.md](stride.md) |

Total: 270 images.
