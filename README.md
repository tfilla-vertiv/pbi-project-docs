# Power BI project docs

Drop this folder into a Power BI project, run it, and get full report documentation: a Markdown site under `docs/` and an HTML copy under `docs-html/`.

The pages cover the semantic model (tables, columns, measures, relationships, Power Query sources) and each report visual that uses model fields.

## What the project folder must look like

The target is a Power BI **project**, not a `.pbix` file. In Power BI Desktop, use **File → Save as → Power BI project (.pbip)**. The folder you unzip into should already contain:

```text
My Report/
  My Report.pbip
  My Report.Report/
  My Report.SemanticModel/
```

A `.pbix` file alone cannot be read. A report that connects to a remote model (`byConnection` in `definition.pbir`) needs the semantic model saved in the project as TMDL as well.

## Report description

To put a description on the overview page, create a text file named `report-description.txt` in the same folder as the `.pbip`. Write the description in that file, exactly as it should appear. A blank line starts a new paragraph.

```text
My Report/
  My Report.pbip
  report-description.txt
  My Report.Report/
  My Report.SemanticModel/
```

The next run copies that text into the overview (`docs/README.md` and `docs-html/README.html`) under **Report description**. If the file is missing or empty, the overview is generated without that section. The text file is left as you wrote it. Edit the text file and run the script again to change the description. Editing the generated HTML does not last, because the next run rewrites it.

## Run it

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) and check **Add python.exe to PATH**.
2. On GitHub, choose **Code → Download ZIP**.
3. Unzip it so this package sits **inside** the project folder. GitHub wraps the files in a folder named `pbi-project-docs-main`. Leave that folder as-is:

```text
My Report/
  My Report.pbip
  My Report.Report/
  My Report.SemanticModel/
  pbi-project-docs-main/
    run.bat
    run.ps1
    tools/
```

You can also unzip the contents directly next to the `.pbip`. Either layout works. The script walks upward from its own folder and uses the nearest `.pbip`.

4. Double-click `run.bat`, or from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pbi-project-docs-main\run.ps1
```

The first run installs `markdown-it-py` for the HTML export if it is not already installed. Later runs just regenerate the pages.

5. Open `docs-html/README.html` next to the `.pbip` (in `My Report/`, not inside the package folder).

Running again rewrites the generated pages. A page under `docs/Tables/` or `docs/Visuals/` is deleted only when this tool created it and that table or visual is gone. Other files in those folders are left alone.

## What you get

| Folder | File | Contents |
| --- | --- | --- |
| `docs/` | `README.md` | Inventory and links |
| `docs/` | `Model.md` | Tables and relationships |
| `docs/` | `Measures.md` | Every measure, its expression, and its dependencies |
| `docs/` | `Sources.md` | Physical sources and Power Query |
| `docs/` | `Visuals.md` | Report pages and the visuals that bind model fields |
| `docs/Tables/` | one page per authored table | Columns, measures, and partitions |
| `docs/Visuals/` | one page per data visual | Fields in use and the role of each one |
| `docs-html/` | the same set as HTML | Open `README.html` in a browser |

Auto-generated date tables (`LocalDateTable_*`, `DateTableTemplate_*`) are omitted. Text boxes, images, shapes, and buttons are counted on the page and do not get their own page.

## Save documentation to Sharepoint

HTML documentation ready to be published should be saved here:

```text
GPDO Sharepoint/
  Power BI Documentation/
    Report Name/
```

If this project already has documentation that you need to update, move the old documentation into a folder called Archive in the above Sharepoint directory, in a sub-folder called DD-MMM-YY (e.g. 10-Nov-26). Live documentation should go directly in the report's directory.
