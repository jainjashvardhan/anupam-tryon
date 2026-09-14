import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:image_picker/image_picker.dart';

/// Point this at your deployed backend Space, e.g.
/// "https://your-username-your-space.hf.space"
const String apiBaseUrl = "https://your-username-your-space.hf.space";

const List<String> categories = [
  "Shirt",
  "T-Shirt",
  "Jacket",
  "Trousers",
  "Suit",
  "Western Dress",
  "Saree",
];

void main() => runApp(const ShopTryOnApp());

class ShopTryOnApp extends StatelessWidget {
  const ShopTryOnApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: "Shop Try-On",
      theme: ThemeData(colorSchemeSeed: Colors.deepPurple, useMaterial3: true),
      home: const TryOnScreen(),
    );
  }
}

class TryOnScreen extends StatefulWidget {
  const TryOnScreen({super.key});

  @override
  State<TryOnScreen> createState() => _TryOnScreenState();
}

class _TryOnScreenState extends State<TryOnScreen> {
  final _picker = ImagePicker();

  XFile? _customerPhoto;
  XFile? _garmentPhoto;
  String _gender = "female";
  String _category = categories.first;

  bool _isGenerating = false;
  Uint8List? _resultBytes;
  String? _errorMessage;

  Future<void> _pickImage({required bool isCustomer}) async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (context) => SafeArea(
        child: Wrap(children: [
          ListTile(
            leading: const Icon(Icons.photo_camera),
            title: const Text("Camera"),
            onTap: () => Navigator.pop(context, ImageSource.camera),
          ),
          ListTile(
            leading: const Icon(Icons.photo_library),
            title: const Text("Gallery"),
            onTap: () => Navigator.pop(context, ImageSource.gallery),
          ),
        ]),
      ),
    );
    if (source == null) return;

    final picked = await _picker.pickImage(source: source, imageQuality: 90);
    if (picked == null) return;

    setState(() {
      if (isCustomer) {
        _customerPhoto = picked;
      } else {
        _garmentPhoto = picked;
      }
      _resultBytes = null;
      _errorMessage = null;
    });
  }

  bool get _canGenerate =>
      _customerPhoto != null && _garmentPhoto != null && !_isGenerating;

  Future<void> _generate() async {
    if (!_canGenerate) return;
    setState(() {
      _isGenerating = true;
      _errorMessage = null;
      _resultBytes = null;
    });

    try {
      final uri = Uri.parse("$apiBaseUrl/generate");
      final request = http.MultipartRequest("POST", uri)
        ..fields["gender"] = _gender
        ..fields["category"] = _category.toLowerCase().replaceAll(" ", "_")
        ..files.add(await http.MultipartFile.fromPath(
            "customer_image", _customerPhoto!.path))
        ..files.add(await http.MultipartFile.fromPath(
            "garment_image", _garmentPhoto!.path));

      final streamedResponse =
          await request.send().timeout(const Duration(seconds: 90));
      final response = await http.Response.fromStream(streamedResponse);

      if (response.statusCode != 200) {
        throw Exception(
            "Server returned ${response.statusCode}: ${response.body}");
      }

      setState(() => _resultBytes = response.bodyBytes);
    } catch (e) {
      setState(() => _errorMessage = "Couldn't generate the image: $e");
    } finally {
      setState(() => _isGenerating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("Shop Try-On")),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Row(
              children: [
                Expanded(
                  child: _PhotoPicker(
                    label: "Customer photo",
                    file: _customerPhoto,
                    onTap: () => _pickImage(isCustomer: true),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: _PhotoPicker(
                    label: "Garment photo",
                    file: _garmentPhoto,
                    onTap: () => _pickImage(isCustomer: false),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: "female", label: Text("Female")),
                ButtonSegment(value: "male", label: Text("Male")),
              ],
              selected: {_gender},
              onSelectionChanged: (s) => setState(() => _gender = s.first),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: _category,
              decoration: const InputDecoration(
                labelText: "Garment category",
                border: OutlineInputBorder(),
              ),
              items: categories
                  .map((c) => DropdownMenuItem(value: c, child: Text(c)))
                  .toList(),
              onChanged: (v) => setState(() => _category = v ?? _category),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: _canGenerate ? _generate : null,
              icon: _isGenerating
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.auto_awesome),
              label: Text(_isGenerating ? "Generating..." : "Generate"),
            ),
            if (_errorMessage != null) ...[
              const SizedBox(height: 16),
              Text(_errorMessage!, style: const TextStyle(color: Colors.red)),
            ],
            if (_resultBytes != null) ...[
              const SizedBox(height: 20),
              ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: Image.memory(_resultBytes!),
              ),
              const SizedBox(height: 8),
              Text(
                "Not saved to this device — only shown here.",
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.grey.shade600, fontSize: 12.5),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PhotoPicker extends StatelessWidget {
  final String label;
  final XFile? file;
  final VoidCallback onTap;

  const _PhotoPicker({
    required this.label,
    required this.file,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: AspectRatio(
        aspectRatio: 3 / 4,
        child: Container(
          decoration: BoxDecoration(
            border: Border.all(color: Colors.grey.shade400),
            borderRadius: BorderRadius.circular(12),
          ),
          child: file == null
              ? Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.add_a_photo_outlined, size: 32),
                    const SizedBox(height: 8),
                    Text(label, textAlign: TextAlign.center),
                  ],
                )
              : ClipRRect(
                  borderRadius: BorderRadius.circular(11),
                  child: Image.file(File(file!.path), fit: BoxFit.cover),
                ),
        ),
      ),
    );
  }
}
