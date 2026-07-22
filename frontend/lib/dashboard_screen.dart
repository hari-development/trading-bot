import 'dart:async';
import 'dart:convert';
import 'dart:io' show Platform;
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:intl/intl.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> with SingleTickerProviderStateMixin {
  WebSocketChannel? _channel;
  bool _isConnected = false;
  bool _isConnecting = false;
  String _systemMode = 'PAPER';

  // Tab index for desktop: 0=Dashboard, 1=Trade History, 2=Live Feed
  // For mobile bottom nav: 0=Dashboard, 1=Live Feed
  int _currentTabIndex = 0;
  String _selectedFilter = 'ALL';

  // Dashboard state
  Map<String, dynamic> _status = {
    'equity': 20000.0,
    'starting_equity': 20000.0,
    'realized_pnl_today': 0.0,
    'trades_today': 0,
    'winning_trades': 0,
    'losing_trades': 0,
    'win_rate_pct': 0.0,
    'avg_profit': 0.0,
    'avg_loss': 0.0,
    'consecutive_losses': 0,
    'consecutive_wins': 0,
    'cooldown_until': null,
    'shutdown_for_day': false,
    'shutdown_reason': '',
    'hard_stop': false,
    'kill_switch_active': false,
    'drawdown_pct': 0.0,
  };

  List<dynamic> _positions = [];
  List<dynamic> _events = [];
  List<dynamic> _tradeHistory = [];
  String _searchQuery = '';

  Timer? _reconnectTimer;
  String _webSocketUrl = '';

  late TabController _desktopTabController;

  @override
  void initState() {
    super.initState();
    _desktopTabController = TabController(length: 3, vsync: this);

    String host = 'localhost';
    if (!kIsWeb && Platform.isAndroid) host = '10.0.2.2';
    _webSocketUrl = 'ws://$host:8765';

    if (kIsWeb || !Platform.environment.containsKey('FLUTTER_TEST')) {
      _connectWebSocket();
    }
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _desktopTabController.dispose();
    _closeWebSocket();
    super.dispose();
  }

  // ── WebSocket ────────────────────────────────────────────────────────────

  void _connectWebSocket() {
    if (_isConnecting || _isConnected) return;
    setState(() => _isConnecting = true);

    final wsUrl = Uri.parse(_webSocketUrl);
    try {
      _channel = WebSocketChannel.connect(wsUrl);
      _channel!.ready.catchError((_) {
        _handleDisconnect();
        return null;
      });
      _channel!.stream.listen(_handleMessage, onDone: _handleDisconnect, onError: (_) => _handleDisconnect());
    } catch (_) {
      _handleDisconnect();
    }
  }

  void _closeWebSocket() {
    _channel?.sink.close();
    _isConnected = false;
    _isConnecting = false;
  }

  void _handleDisconnect() {
    if (mounted) {
      setState(() {
        _isConnected = false;
        _isConnecting = false;
      });
    }
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 5), _connectWebSocket);
  }

  void _handleMessage(dynamic message) {
    if (!mounted) return;
    try {
      final data = json.decode(message.toString());
      final type = data['type'];
      setState(() {
        _isConnected = true;
        _isConnecting = false;
        if (type == 'INITIAL_STATE') {
          _status = Map<String, dynamic>.from(data['status'] ?? {});
          _positions = List<dynamic>.from(data['positions'] ?? []);
          _events = List<dynamic>.from(data['recent_events'] ?? []);
          _tradeHistory = List<dynamic>.from(data['trade_history'] ?? []);
          _systemMode = data['system_mode'] ?? 'PAPER';
        } else if (type == 'STATUS_UPDATE') {
          _status = Map<String, dynamic>.from(data['status'] ?? {});
          _positions = List<dynamic>.from(data['positions'] ?? []);
          _tradeHistory = List<dynamic>.from(data['trade_history'] ?? []);
        } else if (type == 'TRADE_EVENT') {
          final event = data['event'];
          if (event != null) _events.insert(0, event);
        }
      });
    } catch (e) {
      debugPrint('Error decoding WebSocket message: $e');
    }
  }

  void _sendCommand(String action, Map<String, dynamic> params) {
    if (_channel == null || !_isConnected) return;
    _channel!.sink.add(json.encode({'action': action, ...params}));
  }

  // ── Helpers ──────────────────────────────────────────────────────────────

  String _fmtTime(String? iso) {
    if (iso == null || iso.isEmpty) return '–';
    try {
      return DateFormat('HH:mm:ss').format(DateTime.parse(iso).toLocal());
    } catch (_) {
      return iso;
    }
  }

  String _fmtDate(String? iso) {
    if (iso == null || iso.isEmpty) return '–';
    try {
      return DateFormat('dd MMM HH:mm').format(DateTime.parse(iso).toLocal());
    } catch (_) {
      return iso;
    }
  }

  double _num(dynamic v, [double fallback = 0.0]) {
    if (v == null) return fallback;
    if (v is num) return v.toDouble();
    return double.tryParse(v.toString()) ?? fallback;
  }

  int _int(dynamic v, [int fallback = 0]) {
    if (v == null) return fallback;
    if (v is int) return v;
    return int.tryParse(v.toString()) ?? fallback;
  }

  List<dynamic> get _filteredEvents {
    return _events.where((e) {
      final String type = e['event_type'] ?? '';
      if (_selectedFilter == 'ENTRIES' && type != 'ENTRY') return false;
      if (_selectedFilter == 'EXITS' && type != 'EXIT') return false;
      if (_selectedFilter == 'REJECTIONS' && type != 'SIGNAL_REJECTED') return false;
      if (_selectedFilter == 'RISK' && type != 'RISK_EVENT' && type != 'ERROR') return false;
      if (_searchQuery.isNotEmpty) {
        final q = _searchQuery;
        final sym = (e['symbol'] ?? '').toString().toLowerCase();
        final reason = (e['reason'] ?? e['entry_reason'] ?? e['exit_reason'] ?? e['error'] ?? '').toLowerCase();
        final strategy = (e['strategy'] ?? '').toLowerCase();
        return sym.contains(q) || reason.contains(q) || strategy.contains(q) || type.toLowerCase().contains(q);
      }
      return true;
    }).toList();
  }

  // ── Build ────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width;
    final isMobile = width < 860;

    return Scaffold(
      backgroundColor: const Color(0xFF090D16),
      appBar: _buildAppBar(),
      body: isMobile ? _buildMobileBody() : _buildDesktopBody(),
      bottomNavigationBar: isMobile ? _buildBottomNav() : null,
      floatingActionButton: _buildEmergencyFab(),
    );
  }

  AppBar _buildAppBar() {
    return AppBar(
      backgroundColor: const Color(0xFF0F1524),
      elevation: 0,
      titleSpacing: 16,
      title: Row(
        children: [
          Container(
            width: 28,
            height: 28,
            decoration: BoxDecoration(
              gradient: const LinearGradient(colors: [Color(0xFF00E676), Color(0xFF00BCD4)]),
              borderRadius: BorderRadius.circular(6),
            ),
            child: const Icon(Icons.auto_graph, color: Colors.black, size: 16),
          ),
          const SizedBox(width: 10),
          Text(
            'ANTIGRAVITY',
            style: TextStyle(
              fontWeight: FontWeight.w900,
              letterSpacing: 2.0,
              fontSize: 15,
              foreground: Paint()..shader = const LinearGradient(colors: [Color(0xFF00E676), Color(0xFF00BCD4)]).createShader(const Rect.fromLTWH(0, 0, 180, 20)),
            ),
          ),
          const SizedBox(width: 10),
          _modeBadge(),
        ],
      ),
      actions: [
        _connectionIndicator(),
        IconButton(
          icon: const Icon(Icons.settings_outlined, size: 18, color: Colors.white54),
          onPressed: _showSettingsDialog,
          tooltip: 'Connection Settings',
        ),
        const SizedBox(width: 8),
      ],
    );
  }

  Widget _modeBadge() {
    final isLive = _systemMode == 'LIVE';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: (isLive ? Colors.redAccent : Colors.blueAccent).withOpacity(0.3),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: isLive ? Colors.redAccent : Colors.blueAccent, width: 0.8),
      ),
      child: Text(
        _systemMode,
        style: TextStyle(fontSize: 9, fontWeight: FontWeight.bold, color: isLive ? Colors.redAccent : Colors.blueAccent, letterSpacing: 0.5),
      ),
    );
  }

  Widget _connectionIndicator() {
    Color c = _isConnected ? const Color(0xFF00E676) : (_isConnecting ? Colors.orangeAccent : Colors.redAccent);
    String label = _isConnected ? 'LIVE' : (_isConnecting ? 'CONN…' : 'OFFLINE');
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          if (!_isConnected)
            IconButton(
              icon: Icon(_isConnecting ? Icons.sync : Icons.refresh, size: 16, color: Colors.white38),
              onPressed: _isConnecting ? null : _connectWebSocket,
            ),
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(shape: BoxShape.circle, color: c),
          ),
          const SizedBox(width: 5),
          Text(
            label,
            style: TextStyle(fontSize: 9, fontWeight: FontWeight.bold, color: c, letterSpacing: 0.5),
          ),
        ],
      ),
    );
  }

  Widget _buildEmergencyFab() {
    return FloatingActionButton.extended(
      onPressed: _confirmEmergencyStop,
      backgroundColor: const Color(0xFFFF1744),
      foregroundColor: Colors.white,
      icon: const Icon(Icons.stop_circle_outlined, size: 18),
      label: const Text('EMERGENCY HALT', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 11, letterSpacing: 0.8)),
    );
  }

  // ── Desktop Layout ───────────────────────────────────────────────────────

  Widget _buildDesktopBody() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Left: stats + positions + trade history tabs
        Expanded(
          flex: 5,
          child: Column(
            children: [
              _buildDesktopTabs(),
              Expanded(
                child: TabBarView(controller: _desktopTabController, children: [_buildDashboardTab(), _buildTradeHistoryTab(), _buildActivityTab()]),
              ),
            ],
          ),
        ),
        // Right: live feed panel (always visible on desktop)
        Container(
          width: 370,
          decoration: const BoxDecoration(
            border: Border(left: BorderSide(color: Color(0xFF1A2440), width: 1)),
            color: Color(0xFF0A0F1E),
          ),
          child: _buildActivityFeedPanel(),
        ),
      ],
    );
  }

  Widget _buildDesktopTabs() {
    return Container(
      color: const Color(0xFF0F1524),
      child: TabBar(
        controller: _desktopTabController,
        indicatorColor: const Color(0xFF00E676),
        indicatorWeight: 2,
        labelColor: const Color(0xFF00E676),
        unselectedLabelColor: Colors.white38,
        labelStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
        tabs: const [
          Tab(icon: Icon(Icons.dashboard_outlined, size: 16), text: 'Dashboard'),
          Tab(icon: Icon(Icons.history, size: 16), text: 'Trade History'),
          Tab(icon: Icon(Icons.list_alt_outlined, size: 16), text: 'Activity'),
        ],
      ),
    );
  }

  // ── Mobile Layout ────────────────────────────────────────────────────────

  Widget _buildMobileBody() {
    return _currentTabIndex == 0 ? _buildDashboardTab() : _buildActivityFeedPanel();
  }

  BottomNavigationBar _buildBottomNav() {
    return BottomNavigationBar(
      currentIndex: _currentTabIndex,
      onTap: (i) => setState(() => _currentTabIndex = i),
      backgroundColor: const Color(0xFF0F1524),
      selectedItemColor: const Color(0xFF00E676),
      unselectedItemColor: Colors.white30,
      selectedFontSize: 10,
      unselectedFontSize: 10,
      items: const [
        BottomNavigationBarItem(icon: Icon(Icons.dashboard_outlined), label: 'Dashboard'),
        BottomNavigationBarItem(icon: Icon(Icons.list_alt_outlined), label: 'Live Feed'),
      ],
    );
  }

  // ── Tabs ─────────────────────────────────────────────────────────────────

  Widget _buildDashboardTab() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildSystemWarnings(),
          _buildSectionHeader('System Summary'),
          const SizedBox(height: 12),
          _buildStatsGrid(),
          const SizedBox(height: 24),
          _buildUnrealizedPnlBar(),
          const SizedBox(height: 24),
          _buildSectionHeader('Open Positions', subtitle: '${_positions.length} Active'),
          const SizedBox(height: 12),
          _positions.isEmpty
              ? _buildEmptyState('No open positions — scanning watchlist…')
              : ListView.separated(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  itemCount: _positions.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 10),
                  itemBuilder: (_, i) => _buildPositionCard(_positions[i]),
                ),
          const SizedBox(height: 80), // FAB clearance
        ],
      ),
    );
  }

  Widget _buildTradeHistoryTab() {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
          child: _buildSectionHeader('Trade History', subtitle: '${_tradeHistory.length} closed trades today'),
        ),
        Expanded(
          child: _tradeHistory.isEmpty
              ? Center(child: _buildEmptyState('No closed trades yet today.'))
              : ListView.separated(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  itemCount: _tradeHistory.length,
                  separatorBuilder: (_, __) => const SizedBox(height: 8),
                  itemBuilder: (_, i) => _buildTradeHistoryRow(_tradeHistory[i]),
                ),
        ),
      ],
    );
  }

  Widget _buildActivityTab() {
    return _buildActivityFeedPanel();
  }

  // ── Stats Grid ───────────────────────────────────────────────────────────

  Widget _buildStatsGrid() {
    final realizedPnl = _num(_status['realized_pnl_today']);
    final equity = _num(_status['equity'], 20000);
    final startingEquity = _num(_status['starting_equity'], 20000);
    final winRate = _num(_status['win_rate_pct']);
    final drawdown = _num(_status['drawdown_pct']);
    final consecutiveLoss = _int(_status['consecutive_losses']);
    final tradesTotal = _int(_status['trades_today']);
    final openCount = _positions.length;

    double capitalUsed = 0.0;
    if (startingEquity > 0) {
      double usedCapital = _positions.fold(0.0, (acc, p) {
        return acc + (_num(p['entry_price']) * _int(p['quantity']));
      });
      capitalUsed = usedCapital / startingEquity * 100;
    }

    final isPnlPos = realizedPnl >= 0;

    return LayoutBuilder(
      builder: (context, constraints) {
        final cols = constraints.maxWidth > 700 ? 4 : 2;
        return GridView.count(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          crossAxisCount: cols,
          crossAxisSpacing: 12,
          mainAxisSpacing: 12,
          childAspectRatio: constraints.maxWidth > 700 ? 1.7 : 1.8,
          children: [
            _statCard('Account Equity', '₹${equity.toStringAsFixed(0)}', Icons.account_balance_wallet_outlined, const Color(0xFF00BCD4)),
            _statCard(
              "Today's P&L",
              '${isPnlPos ? "+" : ""}₹${realizedPnl.toStringAsFixed(2)}',
              isPnlPos ? Icons.trending_up : Icons.trending_down,
              isPnlPos ? const Color(0xFF00E676) : const Color(0xFFFF1744),
            ),
            _statCard('Win Rate', '${winRate.toStringAsFixed(1)}%', Icons.track_changes_outlined, const Color(0xFFFFD740)),
            _statCard('Drawdown', '${drawdown.toStringAsFixed(2)}%', Icons.arrow_circle_down_outlined, const Color(0xFFFF6D00)),
            _statCard('Trades Today', '$tradesTotal', Icons.receipt_long_outlined, Colors.purpleAccent),
            _statCard('Open Positions', '$openCount', Icons.open_in_new_rounded, const Color(0xFF2979FF)),
            _statCard('Consec. Losses', '$consecutiveLoss', Icons.warning_amber_rounded, consecutiveLoss >= 2 ? Colors.redAccent : Colors.white38),
            _statCard('Capital Used', '${capitalUsed.toStringAsFixed(1)}%', Icons.pie_chart_outline, const Color(0xFF69F0AE)),
          ],
        );
      },
    );
  }

  Widget _statCard(String label, String value, IconData icon, Color color) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF131C2E),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFF1A2440), width: 1),
        boxShadow: [BoxShadow(color: color.withOpacity(0.18), blurRadius: 12, offset: const Offset(0, 4))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Text(
                  label,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 10, color: Colors.white38, fontWeight: FontWeight.w600, letterSpacing: 0.3),
                ),
              ),
              Icon(icon, color: color.withOpacity(0.7), size: 15),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            value,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
          ),
        ],
      ),
    );
  }

  // ── Unrealized P&L Banner ────────────────────────────────────────────────

  Widget _buildUnrealizedPnlBar() {
    if (_positions.isEmpty) return const SizedBox.shrink();
    double totalUnrealized = _positions.fold(0.0, (acc, p) => acc + _num(p['unrealized_pnl']));
    final isPos = totalUnrealized >= 0;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: isPos ? [const Color(0xFF00E676).withOpacity(0.2), const Color(0xFF00BCD4).withOpacity(0.1)] : [const Color(0xFFFF1744).withOpacity(0.2), const Color(0xFFFF6D00).withOpacity(0.1)],
        ),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: isPos ? const Color(0xFF00E676).withOpacity(0.6) : const Color(0xFFFF1744).withOpacity(0.6)),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Icon(isPos ? Icons.trending_up : Icons.trending_down, color: isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744), size: 18),
              const SizedBox(width: 10),
              const Text('Unrealized P&L (Open)', style: TextStyle(color: Colors.white70, fontSize: 12)),
            ],
          ),
          Text(
            '${isPos ? "+" : ""}₹${totalUnrealized.toStringAsFixed(2)}',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744)),
          ),
        ],
      ),
    );
  }

  // ── System Warnings ──────────────────────────────────────────────────────

  Widget _buildSystemWarnings() {
    final isKill = _status['kill_switch_active'] == true;
    final isHard = _status['hard_stop'] == true;
    final isShut = _status['shutdown_for_day'] == true;
    final cooldown = _status['cooldown_until'] as String?;

    bool blocked = isKill || isHard || isShut;
    if (!blocked && cooldown != null) {
      try {
        if (DateTime.now().isBefore(DateTime.parse(cooldown))) {
          blocked = true;
        }
      } catch (_) {}
    }
    if (!blocked) {
      return const SizedBox.shrink();
    }

    String title = 'TRADING PAUSED';
    String msg = '';
    if (isKill) {
      title = '🔴 EMERGENCY STOP ACTIVE';
      msg = 'Kill switch file detected. No new trades until removed manually.';
    } else if (isHard) {
      title = '🚨 MAX DRAWDOWN BREACHED';
      msg = 'System locked. Manual review required before resuming.';
    } else if (isShut) {
      title = '🔒 DAILY LIMIT HIT';
      final reason = _status['shutdown_reason'] as String? ?? '';
      msg = 'Daily circuit breaker triggered. ${reason.isNotEmpty ? "Reason: $reason" : ""}';
    } else if (cooldown != null) {
      title = '⏳ COOLDOWN ACTIVE';
      try {
        final rem = DateTime.parse(cooldown).difference(DateTime.now()).inMinutes;
        msg = 'Max consecutive losses hit. Resuming in $rem min.';
      } catch (_) {
        msg = 'Consecutive loss cooldown active.';
      }
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 18),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.redAccent.withOpacity(0.18),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.redAccent.withOpacity(0.8)),
      ),
      child: Row(
        children: [
          const Icon(Icons.warning_amber_rounded, color: Colors.redAccent, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.redAccent, fontSize: 12),
                ),
                const SizedBox(height: 2),
                Text(msg, style: const TextStyle(color: Colors.white60, fontSize: 11)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ── Positions ────────────────────────────────────────────────────────────

  Widget _buildPositionCard(Map<String, dynamic> pos) {
    final isLong = pos['direction'] == 'LONG';
    final pnl = _num(pos['unrealized_pnl']);
    final pnlPct = _num(pos['unrealized_pnl_pct']);
    final isPos = pnl >= 0;
    final entryPrice = _num(pos['entry_price']);
    final currentPrice = _num(pos['current_price']);
    final sl = _num(pos['stop_loss']);
    final tp = _num(pos['take_profit']);
    final qty = _int(pos['quantity']);
    final symbol = pos['symbol'] ?? '';
    final strategy = (pos['strategy_name'] ?? '').toString().replaceAll('_', ' ');
    final entryTimeStr = _fmtTime(pos['entry_time'] as String?);
    final underlying = pos['underlying_symbol'] as String?;
    final breakeven = pos['breakeven_applied'] == true;
    final partial = pos['partial_booked'] == true;

    // Progress bar: how far from SL to TP are we?
    double progress = 0.0;
    if ((tp - sl).abs() > 0) {
      if (isLong) {
        progress = ((currentPrice - sl) / (tp - sl)).clamp(0.0, 1.0);
      } else {
        progress = ((sl - currentPrice) / (sl - tp)).clamp(0.0, 1.0);
      }
    }

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFF131C2E),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: isPos ? const Color(0xFF00E676).withOpacity(0.4) : const Color(0xFFFF1744).withOpacity(0.4), width: 1),
        boxShadow: [BoxShadow(color: (isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744)).withOpacity(0.12), blurRadius: 16, offset: const Offset(0, 4))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            symbol,
                            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: Colors.white),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        const SizedBox(width: 6),
                        _directionBadge(pos['direction'] ?? ''),
                        if (underlying != null) ...[const SizedBox(width: 4), _chip('OPT', Colors.purpleAccent)],
                        if (breakeven) ...[const SizedBox(width: 4), _chip('BE✓', Colors.blueAccent)],
                        if (partial) ...[const SizedBox(width: 4), _chip('½ Out', Colors.orangeAccent)],
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text('$strategy · ${entryTimeStr.isNotEmpty ? "In at $entryTimeStr" : ""} · Qty: $qty', style: const TextStyle(fontSize: 10, color: Colors.white38)),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    '${isPos ? "+" : ""}₹${pnl.toStringAsFixed(2)}',
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.bold, color: isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744)),
                  ),
                  Text('${isPos ? "+" : ""}${pnlPct.toStringAsFixed(2)}%', style: TextStyle(fontSize: 10, color: isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744))),
                ],
              ),
            ],
          ),

          const SizedBox(height: 12),

          // SL → Current → TP progress bar
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('SL ₹${sl.toStringAsFixed(2)}', style: const TextStyle(fontSize: 9, color: Color(0xFFFF1744))),
                  Text(
                    '₹${currentPrice.toStringAsFixed(2)}',
                    style: const TextStyle(fontSize: 11, color: Colors.white, fontWeight: FontWeight.bold),
                  ),
                  Text('TP ₹${tp.toStringAsFixed(2)}', style: const TextStyle(fontSize: 9, color: Color(0xFF00E676))),
                ],
              ),
              const SizedBox(height: 5),
              ClipRRect(
                borderRadius: BorderRadius.circular(3),
                child: LinearProgressIndicator(
                  value: progress,
                  minHeight: 4,
                  backgroundColor: const Color(0xFFFF1744).withOpacity(0.4),
                  valueColor: AlwaysStoppedAnimation<Color>(progress > 0.5 ? const Color(0xFF00E676) : const Color(0xFFFFD740)),
                ),
              ),
            ],
          ),

          const SizedBox(height: 10),

          // Price details row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              _posDetail('Entry', '₹${entryPrice.toStringAsFixed(2)}'),
              _posDetail('Current', '₹${currentPrice.toStringAsFixed(2)}'),
              if (underlying != null) _posDetail('Underlying', underlying),
            ],
          ),
        ],
      ),
    );
  }

  Widget _posDetail(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontSize: 9, color: Colors.white24)),
        const SizedBox(height: 2),
        Text(
          value,
          style: const TextStyle(fontSize: 10, color: Colors.white70, fontWeight: FontWeight.bold),
        ),
      ],
    );
  }

  Widget _directionBadge(String dir) {
    final isLong = dir == 'LONG';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(color: (isLong ? const Color(0xFF00E676) : const Color(0xFFFF1744)).withOpacity(0.22), borderRadius: BorderRadius.circular(4)),
      child: Text(
        dir,
        style: TextStyle(fontSize: 8, fontWeight: FontWeight.bold, color: isLong ? const Color(0xFF00E676) : const Color(0xFFFF1744)),
      ),
    );
  }

  Widget _chip(String label, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
      decoration: BoxDecoration(
        color: color.withOpacity(0.22),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withOpacity(0.8), width: 0.5),
      ),
      child: Text(
        label,
        style: TextStyle(fontSize: 7, color: color, fontWeight: FontWeight.bold),
      ),
    );
  }

  // ── Trade History ────────────────────────────────────────────────────────

  Widget _buildTradeHistoryRow(Map<String, dynamic> trade) {
    final pnl = _num(trade['pnl']);
    final isPos = pnl >= 0;
    final symbol = trade['symbol'] ?? '';
    final direction = trade['direction'] ?? '';
    final strategy = (trade['strategy'] ?? '').toString().replaceAll('_', ' ');
    final exitReason = trade['exit_reason'] ?? '';
    final entryPrice = _num(trade['entry_price']);
    final exitPrice = _num(trade['exit_price']);
    final qty = _int(trade['quantity']);
    final entryTime = _fmtDate(trade['entry_time'] as String?);
    final exitTime = _fmtTime(trade['exit_time'] as String?);

    return Container(
      margin: const EdgeInsets.only(bottom: 2),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(
        color: const Color(0xFF131C2E),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF1A2440), width: 1),
      ),
      child: Row(
        children: [
          // Colour strip
          Container(
            width: 3,
            height: 40,
            margin: const EdgeInsets.only(right: 12),
            decoration: BoxDecoration(color: isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744), borderRadius: BorderRadius.circular(2)),
          ),
          // Info
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      symbol,
                      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.white),
                    ),
                    const SizedBox(width: 6),
                    _directionBadge(direction),
                    const SizedBox(width: 6),
                    Text(strategy, style: const TextStyle(fontSize: 9, color: Colors.white38)),
                  ],
                ),
                const SizedBox(height: 3),
                Text(
                  '$entryTime → $exitTime · Entry ₹${entryPrice.toStringAsFixed(2)} → Exit ₹${exitPrice.toStringAsFixed(2)} · Qty: $qty · $exitReason',
                  style: const TextStyle(fontSize: 9, color: Colors.white30),
                ),
              ],
            ),
          ),
          // P&L
          Text(
            '${isPos ? "+" : ""}₹${pnl.toStringAsFixed(2)}',
            style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: isPos ? const Color(0xFF00E676) : const Color(0xFFFF1744)),
          ),
        ],
      ),
    );
  }

  // ── Activity Feed ────────────────────────────────────────────────────────

  Widget _buildActivityFeedPanel() {
    final filtered = _filteredEvents;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text(
                    'Live Activity Feed',
                    style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: Colors.white),
                  ),
                  Text('${filtered.length} events', style: const TextStyle(fontSize: 10, color: Colors.white30)),
                ],
              ),
              const SizedBox(height: 8),
              _buildFilterChips(),
              const SizedBox(height: 8),
              SizedBox(
                height: 34,
                child: TextField(
                  onChanged: (v) => setState(() => _searchQuery = v.toLowerCase()),
                  style: const TextStyle(fontSize: 11, color: Colors.white),
                  decoration: InputDecoration(
                    hintText: 'Search symbol, strategy, reason…',
                    hintStyle: const TextStyle(color: Colors.white24, fontSize: 11),
                    prefixIcon: const Icon(Icons.search, size: 14, color: Colors.white24),
                    fillColor: const Color(0xFF131C2E),
                    filled: true,
                    contentPadding: EdgeInsets.zero,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: const BorderSide(color: Color(0xFF1A2440)),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: const BorderSide(color: Color(0xFF1A2440)),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(8),
                      borderSide: const BorderSide(color: Color(0xFF00E676), width: 1),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: filtered.isEmpty
              ? const Center(
                  child: Text('No events.', style: TextStyle(color: Colors.white24, fontSize: 11)),
                )
              : ListView.builder(padding: const EdgeInsets.fromLTRB(16, 0, 16, 16), itemCount: filtered.length, itemBuilder: (_, i) => _buildEventTile(filtered[i])),
        ),
      ],
    );
  }

  Widget _buildFilterChips() {
    final chips = [
      ('ALL', 'All', const Color(0xFF90A4AE)),
      ('ENTRIES', 'Entries', const Color(0xFF00E676)),
      ('EXITS', 'Exits', const Color(0xFFFF1744)),
      ('REJECTIONS', 'Rejected', Colors.orangeAccent),
      ('RISK', 'Risk/Errors', Colors.purpleAccent),
    ];
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: chips.map((c) {
          final sel = _selectedFilter == c.$1;
          final color = c.$3;
          return Padding(
            padding: const EdgeInsets.only(right: 6),
            child: GestureDetector(
              onTap: () => setState(() => _selectedFilter = c.$1),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: sel ? color.withOpacity(0.4) : const Color(0xFF131C2E),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: sel ? color : const Color(0xFF1A2440), width: sel ? 1 : 0.8),
                ),
                child: Text(
                  c.$2,
                  style: TextStyle(fontSize: 9, fontWeight: FontWeight.bold, color: sel ? color : Colors.white38),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildEventTile(Map<String, dynamic> event) {
    final type = event['event_type'] ?? '';
    final symbol = event['symbol'] ?? '';
    final timeStr = _fmtTime(event['timestamp'] as String?);
    final strategy = event['strategy'] ?? '';
    final direction = event['direction'] ?? '';
    final pnl = event['pnl'] != null ? _num(event['pnl']) : null;
    final rr = event['risk_reward'];
    final reason = event['reason'] ?? event['entry_reason'] ?? event['exit_reason'] ?? event['error'] ?? event['event'] ?? '';
    final qty = event['quantity'] != null ? _int(event['quantity']) : null;
    final entryPrice = event['entry_price'] != null ? _num(event['entry_price']) : null;
    final exitPrice = event['exit_price'] != null ? _num(event['exit_price']) : null;

    Color typeColor;
    IconData icon;
    switch (type) {
      case 'ENTRY':
        typeColor = const Color(0xFF00E676);
        icon = Icons.arrow_circle_right_outlined;
        break;
      case 'EXIT':
        typeColor = const Color(0xFFFF1744);
        icon = Icons.arrow_circle_left_outlined;
        break;
      case 'SIGNAL_REJECTED':
        typeColor = Colors.orangeAccent;
        icon = Icons.block_outlined;
        break;
      case 'RISK_EVENT':
        typeColor = Colors.purpleAccent;
        icon = Icons.shield_outlined;
        break;
      case 'ERROR':
        typeColor = Colors.redAccent;
        icon = Icons.error_outline;
        break;
      default:
        typeColor = Colors.white30;
        icon = Icons.info_outline;
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: const Color(0xFF0F1524),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF1A2440), width: 1),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Icon(icon, color: typeColor, size: 11),
                  const SizedBox(width: 4),
                  Text(
                    type,
                    style: TextStyle(color: typeColor, fontSize: 9, fontWeight: FontWeight.bold),
                  ),
                  if (direction.isNotEmpty) ...[const SizedBox(width: 6), _directionBadge(direction)],
                ],
              ),
              Text(timeStr, style: const TextStyle(color: Colors.white24, fontSize: 8)),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (symbol.isNotEmpty)
                      Text(
                        symbol,
                        style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white),
                      ),
                    if (strategy.isNotEmpty) Text(strategy.replaceAll('_', ' '), style: const TextStyle(fontSize: 9, color: Colors.white38)),
                  ],
                ),
              ),
              if (pnl != null)
                Text(
                  '${pnl >= 0 ? "+" : ""}₹${pnl.toStringAsFixed(2)}',
                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: pnl >= 0 ? const Color(0xFF00E676) : const Color(0xFFFF1744)),
                ),
            ],
          ),
          if (reason.isNotEmpty) ...[
            const SizedBox(height: 5),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
              decoration: BoxDecoration(color: const Color(0xFF090D16), borderRadius: BorderRadius.circular(5)),
              child: Text(reason, style: const TextStyle(fontSize: 9, color: Colors.white54, height: 1.4)),
            ),
          ],
          if (qty != null || entryPrice != null || exitPrice != null || rr != null) ...[
            const SizedBox(height: 6),
            Wrap(
              spacing: 4,
              runSpacing: 4,
              children: [
                if (qty != null) _badge('Qty: $qty'),
                if (entryPrice != null) _badge('Entry ₹${entryPrice.toStringAsFixed(2)}'),
                if (exitPrice != null) _badge('Exit ₹${exitPrice.toStringAsFixed(2)}'),
                if (rr != null) _badge('R:R $rr'),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _badge(String label) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
      decoration: BoxDecoration(color: const Color(0xFF1A2440), borderRadius: BorderRadius.circular(4)),
      child: Text(label, style: const TextStyle(fontSize: 8, color: Colors.white38)),
    );
  }

  // ── Shared Helpers ───────────────────────────────────────────────────────

  Widget _buildSectionHeader(String title, {String? subtitle}) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          title,
          style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: Colors.white70),
        ),
        if (subtitle != null) Text(subtitle, style: const TextStyle(fontSize: 10, color: Colors.white30)),
      ],
    );
  }

  Widget _buildEmptyState(String msg) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
      decoration: BoxDecoration(
        color: const Color(0xFF131C2E).withAlpha(80),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFF1A2440)),
      ),
      child: Column(
        children: [
          const Icon(Icons.inbox_outlined, color: Colors.white12, size: 28),
          const SizedBox(height: 8),
          Text(
            msg,
            style: const TextStyle(color: Colors.white24, fontSize: 11),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }

  // ── Dialogs ──────────────────────────────────────────────────────────────

  void _confirmEmergencyStop() {
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: const Color(0xFF131C2E),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Color(0xFFFF1744), width: 1),
        ),
        title: const Row(
          children: [
            Icon(Icons.warning_rounded, color: Color(0xFFFF1744)),
            SizedBox(width: 8),
            Text('CONFIRM EMERGENCY STOP', style: TextStyle(fontSize: 14, color: Color(0xFFFF1744))),
          ],
        ),
        content: const Text(
          'This will immediately:\n'
          '• Activate the kill switch\n'
          '• Block all new entry orders\n'
          '• Leave open positions for manual management\n\n'
          'To restart trading, you must manually delete the KILL_SWITCH file in logs/.',
          style: TextStyle(color: Colors.white70, fontSize: 12, height: 1.6),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel', style: TextStyle(color: Colors.white38)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFFF1744),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            ),
            onPressed: () {
              _sendCommand('emergency_stop', {'reason': 'Emergency Button — Dashboard'});
              Navigator.pop(context);
              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('⚠️ Emergency halt command sent to engine.'), backgroundColor: Color(0xFFFF1744)));
            },
            child: const Text('HALT TRADING', style: TextStyle(fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  void _showSettingsDialog() {
    final ctrl = TextEditingController(text: _webSocketUrl);
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: const Color(0xFF131C2E),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Color(0xFF1A2440)),
        ),
        title: const Row(
          children: [
            Icon(Icons.settings_outlined, color: Color(0xFF00E676), size: 18),
            SizedBox(width: 8),
            Text('Connection Settings', style: TextStyle(fontSize: 14, color: Colors.white)),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Align(
              alignment: Alignment.centerLeft,
              child: Text('WebSocket URL', style: TextStyle(fontSize: 11, color: Colors.white54)),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: ctrl,
              style: const TextStyle(color: Colors.white, fontSize: 12),
              decoration: InputDecoration(
                filled: true,
                fillColor: const Color(0xFF090D16),
                hintText: 'ws://localhost:8765',
                hintStyle: const TextStyle(color: Colors.white24),
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: Color(0xFF1A2440)),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: Color(0xFF00E676)),
                ),
              ),
            ),
            const SizedBox(height: 10),
            const Text(
              '💡 Cloud Hosting (Railway / Render):\n'
              '   Use wss://your-app.up.railway.app\n\n'
              '💡 Local / Emulator:\n'
              '   Use ws://localhost:8765 (Web / iOS)\n'
              '   Use ws://10.0.2.2:8765 (Android)',
              style: TextStyle(color: Colors.white38, fontSize: 10, height: 1.5),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel', style: TextStyle(color: Colors.white38)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF00E676),
              foregroundColor: Colors.black,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            ),
            onPressed: () {
              final url = ctrl.text.trim();
              if (url.isNotEmpty) {
                setState(() => _webSocketUrl = url);
                _closeWebSocket();
                _connectWebSocket();
              }
              Navigator.pop(context);
            },
            child: const Text('Save & Connect', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
          ),
        ],
      ),
    );
  }
}
