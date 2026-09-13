# Shop Try-On (Flutter client)

`pubspec.yaml` and `lib/main.dart` here are the app itself. The Android/iOS platform
scaffolding (which needs the Flutter SDK to generate) isn't included yet - Flutter isn't
installed in the environment this was written in, so `flutter build apk` couldn't be run to
verify it compiles. Do this once you have the Flutter SDK:

## 1. Generate platform scaffolding

From this `mobile/` directory:

```bash
flutter create --project-name shop_tryon --org com.yourshop --platforms android .
```

This adds `android/` (and other platform folders) around the existing `lib/` and
`pubspec.yaml` without overwriting them.

## 2. Add the internet permission

Flutter's Android template doesn't include `INTERNET` by default. Add it to
`android/app/src/main/AndroidManifest.xml`, inside the `<manifest>` tag:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

## 3. Point at your backend

Edit `apiBaseUrl` at the top of `lib/main.dart` to your deployed backend Space URL
(see `../backend/README.md`).

## 4. Install dependencies and build

```bash
flutter pub get
flutter build apk --release
```

The output .apk will be at `build/app/outputs/flutter-apk/app-release.apk`.

## 5. Get it onto associates' phones

No Play Store needed for a handful of internal devices:

1. Share `app-release.apk` (e.g. via a private link, email, or AirDrop/USB).
2. On each phone: open the file, and when prompted, allow "install from this source" /
   "install unknown apps" for the app used to open it (Files, Chrome, etc.) - Android will
   ask this the first time.
3. Tap install.

## Quick local test before building

```bash
flutter run
```

on a connected device/emulator will run it directly without needing a signed release build.
