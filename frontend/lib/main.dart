import 'package:flutter/material.dart';
import 'dart:ui';
import 'dashboard_screen.dart';

void main() {
  runApp(const TradingBotDashboardApp());
}

class AppScrollBehavior extends MaterialScrollBehavior {
  @override
  Set<PointerDeviceKind> get dragDevices => {
    PointerDeviceKind.touch,
    PointerDeviceKind.mouse,
    // Explicitly excluding trackpad to avoid framework assertion bug
  };
}

class TradingBotDashboardApp extends StatelessWidget {
  const TradingBotDashboardApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Antigravity Trading Dashboard',
      scrollBehavior: AppScrollBehavior(),
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.dark,
      darkTheme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF090D16),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF00E676), // Neon Green
          secondary: Color(0xFF2979FF), // Neon Blue
          error: Color(0xFFFF1744), // Hot Red
          surface: Color(0xFF131C2E), // Deep Slate Gray
        ),
        textTheme: const TextTheme(
          displayMedium: TextStyle(fontFamily: 'Inter', fontSize: 32, fontWeight: FontWeight.bold, color: Colors.white),
          titleLarge: TextStyle(fontFamily: 'Inter', fontSize: 20, fontWeight: FontWeight.bold, color: Colors.white),
          bodyLarge: TextStyle(fontFamily: 'Inter', fontSize: 16, color: Color(0xFFB0BEC5)),
          bodyMedium: TextStyle(fontFamily: 'Inter', fontSize: 14, color: Color(0xFF90A4AE)),
        ),
        cardTheme: CardThemeData(
          color: const Color(0xFF131C2E),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: const BorderSide(color: Color(0xFF1F2C46), width: 1),
          ),
          elevation: 4,
        ),
        useMaterial3: true,
      ),
      home: const DashboardScreen(),
    );
  }
}
