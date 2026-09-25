# Mobile (Flutter) — Phase 0 skeleton

Single codebase, role-based UI (`teacher` | `student`), RTL (fa locale).
API base defaults to our own backend (`http://127.0.0.1:8000`, override with
`--dart-define=API_BASE=...`). No vendor SDKs in the client.

```bat
flutter pub get
flutter run
flutter run --dart-define=API_BASE=http://192.168.1.10:8000
```
