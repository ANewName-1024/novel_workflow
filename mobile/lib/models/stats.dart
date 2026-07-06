class BookStats {
  final int total;
  final int approved;
  final int autoPassed;
  final int falsePositive;
  final int humanEdited;
  final int needsRewrite;
  final int pendingReview;

  BookStats({
    this.total = 0,
    this.approved = 0,
    this.autoPassed = 0,
    this.falsePositive = 0,
    this.humanEdited = 0,
    this.needsRewrite = 0,
    this.pendingReview = 0,
  });

  factory BookStats.fromJson(Map<String, dynamic> json) {
    return BookStats(
      total: json['total'] ?? 0,
      approved: json['approved'] ?? 0,
      autoPassed: json['auto_passed'] ?? 0,
      falsePositive: json['false_positive'] ?? 0,
      humanEdited: json['human_edited'] ?? 0,
      needsRewrite: json['needs_rewrite'] ?? 0,
      pendingReview: json['pending_review'] ?? 0,
    );
  }
}

class PipelineHistory {
  final String chapter;
  final String status;
  final String? error;
  final DateTime? startedAt;
  final DateTime? endedAt;
  final int durationSec;

  PipelineHistory({
    required this.chapter,
    required this.status,
    this.error,
    this.startedAt,
    this.endedAt,
    this.durationSec = 0,
  });

  factory PipelineHistory.fromJson(Map<String, dynamic> json) {
    return PipelineHistory(
      chapter: json['chapter'] ?? '',
      status: json['status'] ?? 'unknown',
      error: json['error'],
      startedAt: json['started_at'] != null
          ? DateTime.tryParse(json['started_at'])
          : null,
      endedAt: json['ended_at'] != null
          ? DateTime.tryParse(json['ended_at'])
          : null,
      durationSec: json['duration_sec'] ?? 0,
    );
  }
}
