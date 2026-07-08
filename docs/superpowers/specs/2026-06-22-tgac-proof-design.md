# TGAC 2025 Proof Page Design

## Goal

Create a public GitHub Pages site for `terra901` that verifies and explains a Tencent Games Algorithm Competition 2025 certificate. The page must make the core claim visible immediately: the team won Second Place in the Data-Intelligence Decision Science track.

## Source Material

- Original certificate file: `sealdone_3-2.pdf`
- Rendered certificate preview: generated from the PDF and shown on the page.
- Certificate facts verified from visual inspection:
  - Event: Tencent Games Algorithm Competition 2025
  - Award: Second Place
  - Track: Data-Intelligence Decision Science / 数智决策科学赛道
  - Team: Help Me! KFC Grandpa
  - Members: Haizhen Gao, Gang Xu, Jiyun Chen
  - Issuer shown on certificate: 腾讯游戏算法大赛组委会
  - Date shown on certificate: 2026-01-06
- File integrity proof:
  - SHA-256: `1FD24D09D2E1D5EBBC887B75B59DCE129F63BE14D276B428C01C011C1189128C`

## Proof Boundary

This page is a public proof bundle, not an official Tencent result page. It proves the claim by publishing the original certificate, a preview image, and a reproducible hash. The first version does not include an official public ranking-list URL because no such URL is available in the local materials.

## Recommended Approach

Use a small static GitHub Pages site hosted from `/docs` in a repository named `TAGC-Data-Intelligence-Decision-Science`.

Trade-offs considered:

1. Only upload the PDF to GitHub.
   - Simple, but weak for search and context. Search engines may not index the certificate well, and viewers get no explanation.
2. Static proof page with PDF, image preview, metadata, and hash.
   - Best balance. It is easy to host, searchable, and directly answers "you say second place, where is proof?"
3. Full portfolio page.
   - More polished, but unnecessary and risks burying the proof under unrelated personal content.

The selected design is option 2.

## Page Structure

The page will include:

- Hero section with event name, award, track, team, and a direct "View certificate PDF" link.
- Evidence section with four proof fields: award, track, team, issue date.
- Certificate preview image with alt text and dimensions reserved to avoid layout shift.
- Integrity section showing the PDF SHA-256 hash and a short note explaining what it is.
- Links section with:
  - Original certificate PDF in the repository.
  - Official TGAC website link: `https://tgac.tencent.com/`
  - Expected GitHub repository link: `https://github.com/terra901/TAGC-Data-Intelligence-Decision-Science`
- Disclaimer that the page is a public proof bundle and not an official Tencent page.

## Files

- `docs/index.html`: Single-page proof site.
- `docs/assets/sealdone_3-2.pdf`: Original certificate PDF copied from the workspace root.
- `docs/assets/tgac-2025-second-place-certificate.png`: Rendered certificate preview.
- `README.md`: Repository landing content and deployment instructions.
- `tests/verify-site.mjs`: Node-based checks for required content, links, and assets.

## Visual Design

The design should feel official, restrained, and document-first. It should not look like a marketing landing page. Use a white/light surface, deep navy text, muted blue-purple accents matching the certificate, compact sections, and readable bilingual text.

Accessibility and layout requirements:

- Mobile-first layout.
- No horizontal scroll at 375 px width.
- Body text at least 16 px.
- Strong color contrast.
- Image has descriptive alt text and stable dimensions.
- Links are keyboard focusable and visually obvious.

## Deployment

Target public URL:

`https://terra901.github.io/TAGC-Data-Intelligence-Decision-Science/`

Authentication must use GitHub-supported methods such as a personal access token, browser-based login, or GitHub CLI. A GitHub password must not be used in scripts or saved in the repository.
