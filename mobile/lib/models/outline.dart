class OutlineNode {
  final String id;
  final String title;
  final String? summary;
  final String? pov;
  final List<String> keyEvents;
  final List<String> foreshadow;
  final String? vol;  // server includes this in /api/outline/<book>

  OutlineNode({
    required this.id,
    required this.title,
    this.summary,
    this.pov,
    this.keyEvents = const [],
    this.foreshadow = const [],
    this.vol,
  });

  factory OutlineNode.fromJson(Map<String, dynamic> json) {
    return OutlineNode(
      id: json['id'] ?? '',
      title: json['title'] ?? '未命名',
      summary: json['summary'],
      pov: json['pov'],
      keyEvents: List<String>.from(json['key_events'] ?? []),
      foreshadow: List<String>.from(json['foreshadow'] ?? []),
      vol: json['vol'],
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

  factory Volume.fromJson(Map<String, dynamic> json, {List<OutlineNode> nodes = const []}) {
    // Server's `volumes[].chapters` is a list of display labels
    // (e.g. "第1章 离职通知"), not full chapter objects. We must NOT
    // cast them to Map<String, dynamic>. Instead, the Outline factory
    // resolves nodes from the top-level chapters list and passes them in.
    return Volume(
      id: json['id'] ?? '',
      title: json['title'] ?? '未命名卷',
      summary: json['summary'],
      nodes: nodes,
    );
  }
}

class Outline {
  final String book;
  final List<Volume> volumes;

  Outline({required this.book, this.volumes = const []});

  factory Outline.fromJson(Map<String, dynamic> json) {
    // Top-level chapters carry `vol`; group them by vol id.
    final allChapters = (json['chapters'] as List<dynamic>?)
            ?.whereType<Map<String, dynamic>>()
            .map(OutlineNode.fromJson)
            .toList() ??
        const <OutlineNode>[];

    final rawVols = (json['volumes'] as List<dynamic>?)
            ?.whereType<Map<String, dynamic>>()
            .toList() ??
        const <Map<String, dynamic>>[];

    final vols = rawVols.map((v) {
      final vid = v['id'] as String? ?? '';
      final nodes = allChapters.where((n) => n.vol == vid).toList();
      return Volume.fromJson(v, nodes: nodes);
    }).toList();

    return Outline(
      book: json['book'] ?? '',
      volumes: vols,
    );
  }
}