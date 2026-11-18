# BingX Futures Bot

Symulator pozycji long i short na kontraktach perpetual USDT. Korzysta z publicznych danych BingX i wirtualnego salda. Wymaga Pythona 3.10+ oraz połączenia z API BingX.

## Uruchomienie

W terminalu otwartym w folderze projektu wpisz:

```sh
python3 bot.py --once
```

Aby skanować w pętli, użyj `python3 bot.py`. Zatrzymaj bota przez Ctrl+C. Na Windows możesz użyć `py` zamiast `python3`.

## Strategia

Bot wybiera domyślnie 20 kontraktów o najwyższym obrocie z 24 godzin i nadal obserwuje kontrakty z otwartą pozycją. Przecięcie EMA 9 powyżej EMA 21 na zamkniętej świecy 15-minutowej daje sygnał long. Przecięcie w dół daje sygnał short.

Symulacja zaczyna od 1000 USDT. Pozycja może mieć do 10% zrealizowanego salda, bez dźwigni. Bot trzyma maksymalnie trzy pozycje. Dzienny próg zrealizowanej straty wynosi 2% salda z początku dnia UTC. Po jego osiągnięciu bot wstrzymuje nowe wejścia, dopóki wynik dnia znajduje się poniżej progu.

Bot sprawdza stop 1%, cel 2% i sygnał przeciwny na zamknięciu świecy. Symulowane wykonanie uwzględnia poślizg 0,02% oraz prowizję 0,05% po każdej stronie. Model pomija ruch wewnątrz świecy, funding i likwidację. Po przerwie bot wznawia działanie od najnowszej zamkniętej świecy.

## Ustawienia

- `--pairs 20` ustawia liczbę skanowanych kontraktów.
- `--capital 1000` ustawia saldo przy tworzeniu nowej bazy.
- `--db paper.sqlite3` ustawia plik stanu, otwartych pozycji i historii transakcji.
