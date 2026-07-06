class OutlineNode {
  final String id;
  final String title;
  final String? summary;
  final String? pov;
  final List<String> keyEvents;
  final List<String> foreshadow;

  OutlineNode({
    required this.id,
    required this.title,
    this.summary,
    this.pov,
    this.keyEvents = const [],
    this.foreshadow = const [],
  });

  factory OutlineNode.fromJson(Map<String, dynamic> json) {
    return OutlineNode(
      id: json['id'] ?? '',
      title: json['title'] ?? '未命名',
      summary: json['summary'],
      pov: json['pov'],
      keyEvents: List<String>.from(json['key_events'] ?? []),
      foreshadow: List<String>.from(json['foreshadow'] ?? []),
    );
  }
}

class Volume {
  final String id;
  final String title;
  final String? summary;
  final List<OutlineNode> nodes;

  Volume({
    required this.id,
    required this.title,
    this.summary,
    this.nodes = const [],
  });

  factory Volume.fromJson(Map<String, dynamic> json) {
    return Volume(
      id: json['id'] ?? '',
      title: json['title'] ?? '未命名卷',
      summary: json['summary'],
      nodes: (json['chapters'] as List<dynamic>?)
              ?.map((e) => OutlineNode.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }
}

class Outline {
  final String book;
  final List<Volume> volumes;

  Outline({required this.book, this.volumes = const []});

  factory Outline.fromJson(Map<String, dynamic> json) {
    return Outline(
      book: json['book'] ?? '',
      volumes: (json['volumes'] as List<dynamic>?)
              ?.map((e) => Volume.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }
}
