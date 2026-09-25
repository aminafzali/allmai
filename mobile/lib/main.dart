import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:go_router/go_router.dart';

/// Phase-0 skeleton: one codebase, two roles (teacher | student).
/// Full screens land in Phase 12; API base points at our own backend only.
enum AppRole { teacher, student }

const apiBase = String.fromEnvironment('API_BASE',
    defaultValue: 'http://127.0.0.1:8000');

final _router = GoRouter(
  routes: [
    GoRoute(path: '/', builder: (c, s) => const RoleGate()),
    GoRoute(path: '/teacher', builder: (c, s) => const TeacherHome()),
    GoRoute(path: '/student', builder: (c, s) => const StudentHome()),
  ],
);

void main() => runApp(const AllMaiApp());

class AllMaiApp extends StatelessWidget {
  const AllMaiApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'AllMai',
      locale: const Locale('fa'),
      supportedLocales: const [Locale('fa'), Locale('en')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      theme: ThemeData(useMaterial3: true),
      builder: (context, child) =>
          Directionality(textDirection: TextDirection.rtl, child: child!),
      routerConfig: _router,
    );
  }
}

class RoleGate extends StatelessWidget {
  const RoleGate({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('AllMai — ورود')),
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            FilledButton(
              onPressed: () => context.go('/teacher'),
              child: const Text('ورود معلم'),
            ),
            const SizedBox(height: 12),
            FilledButton.tonal(
              onPressed: () => context.go('/student'),
              child: const Text('ورود دانش‌آموز'),
            ),
          ],
        ),
      ),
    );
  }
}

class TeacherHome extends StatelessWidget {
  const TeacherHome({super.key});

  @override
  Widget build(BuildContext context) {
    // Phase 12: login, home, assistants, create lesson plan, detail, AI chat.
    return Scaffold(
      appBar: AppBar(title: const Text('پنل معلم')),
      body: const Center(child: Text('اسکلت فاز صفر — طرح درس در فاز ۹')),
    );
  }
}

class StudentHome extends StatelessWidget {
  const StudentHome({super.key});

  @override
  Widget build(BuildContext context) {
    // Phase 12: login, home, academic coach, study plan, progress, AI chat.
    return Scaffold(
      appBar: AppBar(title: const Text('پنل دانش‌آموز')),
      body: const Center(child: Text('اسکلت فاز صفر — مربی تحصیلی در فاز ۱۰')),
    );
  }
}
