# Third-Party Notices

HealthManager's own code is MIT-licensed (see [LICENSE](LICENSE)). It relies on the third-party software and data below, each under its own license. None of the libraries are copied into this repository - Python packages are installed from PyPI via `requirements.txt`, and frontend libraries load from the jsDelivr CDN.

## Data sources

### Fineli® - Finnish food composition database

- **Provider:** Finnish Institute for Health and Welfare (THL) - https://fineli.fi/
- **License:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
- **Attribution:** Nutritional data: Finnish Institute for Health and Welfare (THL), Fineli®.
- **Changes made:** HealthManager imports the Fineli Open Data CSV packages into its own database. Energy is converted from kJ to kcal (÷ 4.184) and salt from mg to g; the values are otherwise unmodified. Recipe values are calculated from Fineli ingredient data.
- **Not included:** this repository contains no Fineli data. Each user downloads it from https://fineli.fi/fineli/en/avoin-data.
- Fineli® is a registered trademark of THL. HealthManager is independent and is not affiliated with or endorsed by THL.

### Garmin Connect

- Activity and health data is fetched from the user's own Garmin Connect account via the unofficial [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library (see below).
- Garmin® and Garmin Connect™ are trademarks of Garmin Ltd. or its subsidiaries. HealthManager is not affiliated with, endorsed by, or supported by Garmin. It uses no official Garmin API.
- Using an unofficial API may conflict with Garmin's terms of use. Use it only with your own account and at your own risk.

## Python packages

| Package | License | Project |
| --- | --- | --- |
| Django | BSD-3-Clause | https://www.djangoproject.com/ |
| garminconnect | MIT - Copyright (c) 2020-2026 Ron Klinkien | https://github.com/cyberjunky/python-garminconnect |
| garth (used by garminconnect) | MIT | https://github.com/matin/garth |
| python-dotenv | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| whitenoise | MIT | https://github.com/evansd/whitenoise |
| gunicorn | MIT | https://gunicorn.org/ |
| requests | Apache-2.0 | https://github.com/psf/requests |
| curl_cffi | MIT | https://github.com/lexiforest/curl_cffi |
| pydantic | MIT | https://github.com/pydantic/pydantic |
| rich | MIT | https://github.com/Textualize/rich |

Other packages in `requirements.txt` are transitive dependencies of the above, under permissive licenses (MIT, MIT-0, BSD-2/3-Clause, ISC, Apache-2.0, PSF-2.0, or MPL-2.0 for `certifi`).

## Frontend libraries (loaded from CDN)

| Library | Version | License | Project |
| --- | --- | --- | --- |
| Bootstrap | 5.3.3 | MIT | https://getbootstrap.com/ |
| Bootstrap Icons | 1.11.3 | MIT | https://icons.getbootstrap.com/ |
| Chart.js | 4.5.1 | MIT | https://www.chartjs.org/ |
