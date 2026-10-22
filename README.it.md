# google-health-to-intervals (in breve)

Copia i dati di **Google Health** (Fitbit, Pixel Watch, Fitbit Air) su
**intervals.icu**, quattro volte al giorno. Pensato per chi non può usare le app
Android (soprattutto chi ha l'**iPhone**). Gira **gratis sul tuo account GitHub**
(GitHub Actions): niente Raspberry Pi, niente server.

**Cosa copia:** sonno (`sleepSecs`), battito a riposo (`restingHR`), HRV (`hrv`),
peso (`weight`), SpO2 (`spO2`), passi (`steps`), VO2max (`vo2max`),
respirazione (`respiration`). Il punteggio del sonno non è disponibile nella
Google Health API. I valori vuoti non vengono mai inviati.

## Come si installa

La guida completa, passo per passo, è nel [README in inglese](README.md). In sintesi:

1. **Use this template** → crea il repository come **privato**.
2. Crea un progetto su Google Cloud e abilita la **Google Health API**.
3. Schermata di consenso: **External**, stato **Testing**, aggiungi te stesso
   come utente di test e i tre permessi `googlehealth.*.readonly`.
4. Crea un client OAuth di tipo **Desktop app**.
5. Su intervals.icu: Settings → Developer Settings → chiave API e ID atleta.
6. Nel repository: Settings → Secrets and variables → Actions → aggiungi
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `INTERVALS_API_KEY`,
   `INTERVALS_ATHLETE_ID`. Consigliata la variabile `GHTI_TIMEZONE` = `Europe/Rome`.
7. Actions → **Re-authorize** → Run workflow (casella vuota) → apri il link nel
   riepilogo → approva → il browser mostra un errore (è normale) → copia
   l'indirizzo intero → di nuovo **Re-authorize**, incollalo in `redirect_url`.

## Ogni settimana

Google fa scadere l'autorizzazione delle app in "Testing" dopo 7 giorni. Il
sesto giorno il workflow **Reminder** diventa rosso apposta e GitHub ti manda
un'email con il link: ripeti il punto 7 (un minuto). Se lo salti, la sync si
ferma ma i giorni persi vengono recuperati dopo la nuova autorizzazione.

## Privacy

I dati vanno solo da Google a intervals.icu. Nessun server nostro, nessun dato
di salute salvato nel repository. Il token è cifrato in `state/token.enc` con una
chiave derivata dai tuoi segreti.

Licenza MIT.
