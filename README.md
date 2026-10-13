# Handy Apps Autopilot

Automat do Instagrama dla Ready72h i BikepackPilot. Dwa razy w tygodniu agenci tworzą po jednym poście na apkę, planują publikację i dają Ci czas na wstrzymanie. Bez Twojej reakcji wszystko dzieje się samo.

```
pon i czw ~18:00   Generuj posty   → 2 agenci piszą (BikepackPilot + Ready72h), sami wybierają rolkę / pokaz slajdów — zawsze wideo z muzyką
                                    → kontrola jakości → grafiki / rolka → zaplanowanie w Zernio
                                    → zgłoszenie (Issue) z podglądem dla Ciebie, gotowe przed 20:00
Ty                 brak komentarza = post wychodzi · dowolny komentarz (poza „ok”) = post wstrzymany
wt i pt            Zernio publikuje: BikepackPilot 7:00, Ready72h 12:00 (czas polski)
wt i pt            Sprawdź publikacje → link do posta w zgłoszeniu, zgłoszenie zamknięte; błąd = mail z GitHuba
niedziela          Statystyki → reports/stats.md
```

## Konfiguracja (jednorazowo, ok. 1 h)

Nazwy menu w Zernio i GitHub zmieniają się co jakiś czas; jeśli coś wygląda inaczej, szukaj tych samych pojęć.

### 1. GitHub: uprawnienia Actions
Repo → **Settings → Actions → General → Workflow permissions**:
- zaznacz **Read and write permissions**
- zaznacz **Allow GitHub Actions to create and approve pull requests**

### 2. Instagram przez Zernio
Publikacja idzie przez [Zernio](https://zernio.com) (darmowe dla 1–2 kont): Zernio ma zatwierdzoną aplikację Meta, a Instagram łączysz logowaniem IG — bez Facebooka.
1. Załóż konto na zernio.com, wybierz *I'll use it myself* i połącz Instagram (uprawnienia: profil, publikowanie, statystyki).
2. Wybierz *With the API* i skopiuj klucz API (`sk_...`).
3. Pliki są wysyłane do Zernio bezpośrednio, więc repo może być prywatne.

### 4. Claude API
[console.anthropic.com](https://console.anthropic.com) → API Keys → nowy klucz. Ustaw limit wydatków (np. 10 USD/mies.).

### 5. Sekrety repo
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Sekret | Skąd |
|---|---|
| `ANTHROPIC_API_KEY` | krok 4 |
| `ZERNIO_API_KEY` | krok 2 |
| `ZERNIO_ACCOUNT_ID` | opcjonalnie — wykrywane automatycznie (jedyne konto IG w Zernio) |

Opcjonalnie w zakładce **Variables**: `CLAUDE_MODEL` (domyślnie `claude-sonnet-5-5`).

### 6. Marka i materiały
- `config/brand.yaml` — kolory i czcionki obu apek (przeniesione z Twoich filmów), handle IG.
- `config/apps.yaml` — **fakty o produktach**. Agent nie napisze nic spoza tej listy; uzupełnij BikepackPilot.
- `assets/clips/<apka>/` — nagrania ekranu apek + `clips.yaml` z opisem każdego klipu; agent dobiera klip do tekstu. Nowy klip = plik MP4 + linijka w clips.yaml.
- `assets/music/` — biblioteka muzyki (Pixabay, bez praw autorskich). Każdy post to rolka z muzyką: agent losuje utwór nieużyty ostatnio, wspólny dla obu apek. Bez muzyki post się nie wygeneruje. Nowy utwór = plik MP3 w tym folderze.
- `config/topics.yaml` — bank tematów; gdy się skończy, agent wymyśla nowe.
- `config/schedule.yaml` — rytm (co 3 dni), data startu, godzina publikacji.

### 7. Pierwszy test
**Actions → Generuj posty → Run workflow** (np. app `ready72h`, format `carousel`). Po kilku minutach pojawi się PR z podglądem. Scal go i uruchom **Publikuj na Instagram** z opcją *now*.

## Na co dzień
- Pon i czw wieczorem dostajesz z GitHuba powiadomienie o 2 nowych zgłoszeniach (Issues) z podglądem postów.
- **Nic nie robisz** → posty wychodzą we wt / pt (BikepackPilot 7:00, Ready72h 12:00).
- **Komentarz pod zgłoszeniem** (np. „stop”) → ten post zostaje wstrzymany i nie wyjdzie. Komentarz „ok” nic nie zmienia.
- Chcesz nową wersję wstrzymanego posta? **Actions → Generuj posty → Run workflow** (apka + data).
- Godziny i dni zmieniasz w `config/schedule.yaml` (godziny) i `.github/workflows/generate.yml` (dni generowania).
- Awaryjnie: **Actions → Narzędzia publikacji** — test połączenia albo natychmiastowa publikacja wybranego posta.

## Koszty (orientacyjnie)
Claude API: kilka groszy za post (2–4 wywołania). GitHub Actions, Zernio (1 konto): w darmowych limitach przy tym rytmie.

## Lokalnie
```
pip install -r requirements.txt && python -m playwright install chromium
python src/generate.py --sample --app ready72h --format carousel --date 2026-10-12   # bez API
python src/publish.py --dry-run --now
```
