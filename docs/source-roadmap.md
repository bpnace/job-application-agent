# Source Roadmap

V1 stays limited to verified public sources and guarded local application automation. The following sources are candidates for later adapters after source-specific access rules, rate limits, robots/public-page behavior, and login/CAPTCHA boundaries are checked.

## Active V1 Public Sources

- Optional SerpApi Google organic search as a sparse booster
- Arbeitnow API
- Arbeitsagentur public jobs API
- Remote OK public API
- Remotive public API
- StepStone public search pages with a dedicated single-job parser
- LinkedIn public search pages with a dedicated job-view parser
- Freelancermap public remote listing
- Generic public jobboard adapter:
  - get-in-it
  - DEVjobs.de
  - Heise Jobs
  - IT-Jobs.de
  - Golem Jobs
  - Jobware
  - Kimeta
  - Freelance.de
  - Workwise
  - Truffls
  - EU Remote Jobs
  - GermanTechJobs
  - Instaffo
  - The Local Jobs
  - Arbeitnow public page

## Parked Because Of Current 403 Blocks

- jobvector
- Malt

## Search Portals To Evaluate Or Keep As Fallback

- XING Jobs
- Indeed.de
- Heise Jobs
- IT-Jobs.de
- Deutsche Startups Jobs
- Gründerszene Jobs
- Startup Jobs DE
- Wellfound
- Monster.de
- DEVjobs.de

## Direct Jobboards

- Google Jobs
- Company ATS pages found through SerpApi, StepStone, LinkedIn, Arbeitnow or public job boards

## Adapter Rules

- Keep all adapters public-only and fail-closed.
- Do not add login, CAPTCHA handling or email sending.
- Form submission must stay behind an explicit per-job submit command.
- Prefer documented APIs or public feeds over browser scraping.
- Add source-specific URL host policies and redirect tests before enabling a live adapter.
- Keep per-host pauses and the global candidate cap active for every source.
