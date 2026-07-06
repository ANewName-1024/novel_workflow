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

/// One line in the diff output (legacy harness).
class DiffEntry {
  final String text;
  final bool isInsert;

  DiffEntry({required this.text, required this.isInsert});

  factory DiffEntry.fromJson(Map<String, dynamic> json) {
    final t = json['text'] ?? json['line'] ?? '';
    final kind = (json['type'] ?? json['op'] ?? json['kind'] ?? '').toString();
    final isInsert = kind == '+' || kind == 'insert' || kind == 'add';
    return DiffEntry(text: t.toString(), isInsert: isInsert);
  }
}

/// Backend returns `{diff:[], has_diff:false, stats:null}` for /api/diff/<book>/<ch>.
class ChapterDiff {
  final List<DiffEntry> entries;
  final bool hasDiff;

  ChapterDiff({required this.entries, required this.hasDiff});

  factory ChapterDiff.fromJson(Map<String, dynamic> json) {
    final raw = (json['diff'] as List<dynamic>?) ?? const [];
    final entries = raw.map((e) => DiffEntry.fromJson(e as Map<String, dynamic>)).toList();
    final has = json['has_diff'] == true || entries.isNotEmpty;
    return ChapterDiff(entries: entries, hasDiff: has);
  }
}
