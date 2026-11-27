from django.db import migrations
from markdown_it import MarkdownIt


def convert_text_blocks(apps, schema_editor):
    PageBlock = apps.get_model("content", "PageBlock")
    markdown = MarkdownIt("commonmark", {"html": False}).disable("image")
    blocks = PageBlock.objects.using(schema_editor.connection.alias).filter(kind="text")
    for block in blocks.iterator():
        blocks.filter(pk=block.pk).update(text=markdown.render(block.text))


class Migration(migrations.Migration):
    dependencies = [("content", "0004_pageblock")]

    operations = [migrations.RunPython(convert_text_blocks)]
