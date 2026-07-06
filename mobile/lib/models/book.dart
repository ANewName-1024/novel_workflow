class Book {
  final String name;
  final String displayName;
  final int totalChapters;
  final int pendingReviews;
  final int approved;
  final int rejected;
  final String? lastPipelineStatus;

  Book({
    required this.name,
    required this.displayName,
    this.totalChapters = 0,
    this.pendingReviews = 0,
    this.approved = 0,
    this.rejected = 0,
    this.lastPipelineStatus,
  });

  factory Book.fromJson(Map<String, dynamic> json, String name) {
    return Book(
      name: name,
      displayName: json['display_name'] ?? name,
      totalChapters: json['total_chapters'] ?? 0,
      pendingReviews: json['pending_reviews'] ?? 0,
      approved: json['approved'] ?? 0,
      rejected: json['rejected'] ?? 0,
      lastPipelineStatus: json['last_pipeline_status'],
    );
  }
}
