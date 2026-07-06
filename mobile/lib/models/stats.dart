class BookStats {
  final int totalChapters;
  final int approvedChapters;
  final int pendingChapters;
  final int rejectedChapters;
  final int totalWords;
  final double avgChapterWords;
  final int pipelineRuns;
  final int pipelineFails;

  BookStats({
    this.totalChapters = 0,
    this.approvedChapters = 0,
    this.pendingChapters = 0,
    this.rejectedChapters = 0,
    this.totalWords = 0,
    this.avgChapterWords = 0,
    this.pipelineRuns = 0,
    this.pipelineFails = 0,
  });

  factory BookStats.fromJson(Map<String, dynamic> json) {
    return BookStats(
      totalChapters: json['total_chapters'] ?? 0,
      approvedChapters: json['approved_chapters'] ?? 0,
      pendingChapters: json['pending_chapters'] ?? 0,
      rejectedChapters: json['rejected_chapters'] ?? 0,
      totalWords: json['total_words'] ?? 0,
      avgChapterWords: (json['avg_chapter_words'] ?? 0).toDouble(),
      pipelineRuns: json['pipeline_runs'] ?? 0,
      pipelineFails: json['pipeline_fails'] ?? 0,
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
