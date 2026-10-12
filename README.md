# Handy Apps Autopilot

Automat do Instagrama dla Ready72h i BikepackPilot. Agenci co 3 dni (na apkę, z przesunięciem) tworzą karuzelę albo rolkę, sprawdzają ją i dają Ci do akceptacji jako Pull Request. Scalenie PR = publikacja w zaplanowanym dniu.

```
codziennie 7:17   Generuj posty  → Claude pisze → Claude sprawdza → render PNG/MP4 → Pull Request
Ty (telefon)      Merge = akceptuję · Close = odrzucam · edycja post.json = poprawka podpisu
codziennie 18:05  Publikuj      → Instagram Graph API (zdjęcia z publicznego repo, filmy wysyłane wprost) → published.json
niedziela         Statystyki    → reports/stats.md
```

## Konfiguracja (jednorazowo, ok. 1 h)

Nazwy menu w Meta i GitHub zmieniają się co jakiś czas; jeśli coś wygląda inaczej, szukaj tych samych pojęć.

### 1. GitHub: uprawnienia Actions
Repo → **Settings → Actions → General → Workflow permissions**:
- zaznacz **Read and write permissions**
- zaznacz **Allow GitHub Actions to create and approve pull requests**

### 2. Instagram + Meta (token do publikacji)
1. Konto IG musi być **firmowe** i połączone ze **stroną na Facebooku** (Meta Business Suite → Ustawienia → Konta Instagram).
2. [developers.facebook.com](https://developers.facebook.com) → **Create App** → typ **Business**. Dodaj produkt **Instagram** (wariant *API setup with Facebook login*).
3. Token bez wygasania (zalecane): **business.facebook.com → Ustawienia → Użytkownicy systemowi** → dodaj użytkownika (Admin) → przypisz mu stronę FB, konto IG i aplikację → **Generuj token** z uprawnieniami:
   `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement`, `business_management`.
4. ID konta IG: w [Graph API Explorer](https://developers.facebook.com/tools/explorer) z tym tokenem wywołaj
   `me/accounts?fields=instagram_business_account{id,username}` → skopiuj `instagram_business_account.id`.

Dla własnego konta aplikacja zwykle działa w trybie deweloperskim, bez przeglądu Meta (App Review).

### 3. Repo publiczne
Instagram pobiera zdjęcia z publicznego linku, więc repo musi być **publiczne** (Settings → Danger Zone → Change visibility). Sekrety pozostają ukryte. Filmy są wysyłane do Instagrama bezpośrednio.

### 4. Claude API
[console.anthropic.com](https://console.anthropic.com) → API Keys → nowy klucz. Ustaw limit wydatków (np. 10 USD/mies.).

### 5. Sekrety repo
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Sekret | Skąd |
|---|---|
| `ANTHROPIC_API_KEY` | krok 4 |
| `IG_ACCESS_TOKEN` | krok 2.3 |
| `IG_USER_ID` | krok 2.4 |

Opcjonalnie w zakładce **Variables**: `CLAUDE_MODEL` (domyślnie `claude-sonnet-5-5`), `GRAPH_API_VERSION` (domyślnie `v23.0`).

### 6. Marka i materiały
- `config/brand.yaml` — kolory i czcionki obu apek (przeniesione z Twoich filmów), handle IG.
- `config/apps.yaml` — **fakty o produktach**. Agent nie napisze nic spoza tej listy; uzupełnij BikepackPilot.
- `assets/clips/<apka>/` — nagrania ekranu apek + `clips.yaml` z opisem każdego klipu; agent dobiera klip do tekstu. Nowy klip = plik MP4 + linijka w clips.yaml.
- `assets/music/` — muzyka bez praw autorskich (MP3), na zmianę w rolkach. Bez plików rolka będzie cicha.
- `config/topics.yaml` — bank tematów; gdy się skończy, agent wymyśla nowe.
- `config/schedule.yaml` — rytm (co 3 dni), data startu, godzina publikacji.

### 7. Pierwszy test
**Actions → Generuj posty → Run workflow** (np. app `ready72h`, format `carousel`). Po kilku minutach pojawi się PR z podglądem. Scal go i uruchom **Publikuj na Instagram** z opcją *now*.

## Na co dzień
- Powiadomienie o nowym PR przychodzi z aplikacji GitHub. Podgląd slajdów lub rolki jest w opisie.
- **Merge** → post wyjdzie w dniu publikacji o 18:05 (albo od razu, jeśli termin już minął).
- **Close** → post odrzucony; temat wróci w kolejnym cyklu.
- Poprawka podpisu/hashtagów: edytuj `post.json` w PR (ikona ołówka) i dopiero scal.
- Grafiki lub rolkę chcesz inne? Zamknij PR i uruchom **Generuj posty** ręcznie dla tej apki i daty.

## Koszty (orientacyjnie)
Claude API: kilka groszy za post (2–4 wywołania). GitHub Actions (publiczne repo), Graph API: w darmowych limitach przy tym rytmie.

## Lokalnie
```
pip install -r requirements.txt && python -m playwright install chromium
python src/generate.py --sample --app ready72h --format carousel --date 2026-10-12   # bez API
python src/publish.py --dry-run --now
```
