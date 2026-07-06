class Chapter {
  final String id;
  final String title;
  final String? content;
  final String status;
  final String? reviewStatus;
  final DateTime? updatedAt;

  Chapter({
    required this.id,
    required this.title,
    this.content,
    this.status = 'draft',
    this.reviewStatus,
    this.updatedAt,
  });

  factory Chapter.fromJson(Map<String, dynamic> json) {
    return Chapter(
      id: json['id'] ?? json['ch'] ?? '',
      title: json['title'] ?? '未命名章节',
      content: json['content'],
      status: json['status'] ?? 'draft',
      reviewStatus: json['review_status'],
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'])
          : null,
    );
  }

  factory Chapter.fromId(String id) {
    return Chapter(id: id, title: id);
  }
}

class ChapterDiff {
  final String left;
  final String right;
  final int linesAdded;
  final int linesRemoved;

  ChapterDiff({
    required this.left,
    required this.right,
    this.linesAdded = 0,
    this.linesRemoved = 0,
  });

  factory ChapterDiff.fromJson(Map<String, dynamic> json) {
    return ChapterDiff(
      left: json['left'] ?? '',
      right: json['right'] ?? '',
      linesAdded: json['lines_added'] ?? 0,
      linesRemoved: json['lines_removed'] ?? 0,
    );
  }
}
