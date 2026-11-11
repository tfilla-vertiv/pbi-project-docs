# Power BI project docs

Drop this folder into a Power BI project, run it, and get full report documentation: a Markdown site under `docs/` and an HTML copy under `docs-html/`.

The pages cover the semantic model (tables, columns, measures, relationships, Power Query sources) and each report visual that uses model fields.

## 1. Save your report as a pbip

The target is a Power BI **project**, not a `.pbix` file. Open your Power BI report in desktop. Click Save As and save the report as pbip. Save it in its own directory, with no other files in the folder. The resulting files should be:

```text
My Report/
  My Report.pbip
  My Report.Report/
  My Report.SemanticModel/
```

## 2. Create report description

To put a description on the overview page, create a text file named `report-description.txt` in the same folder as the `.pbip`. Write the description in that file, exactly as it should appear. A blank line starts a new paragraph.

```text
My Report/
  My Report.pbip
  report-description.txt
  My Report.Report/
  My Report.SemanticModel/
```

## 3. Run script

On this GitHub repository, choose **Code → Download ZIP**. Unzip it so this package sits **inside** the project folder. GitHub wraps the files in a folder named `pbi-project-docs-main`. Leave that folder as-is:

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

Double-click `run.bat` to run the scripts. The command prompt window will inform you if the script ran successfully, and how many documents it created.

Open `docs-html/README.html` next to the `.pbip` (in `My Report/`, not inside the package folder).

Running again rewrites the generated pages. A page under `docs/Tables/` or `docs/Visuals/` is deleted only when this tool created it and that table or visual is gone. Other files in those folders are left alone.

## 4. Verify results

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

## 5. Save documentation to Sharepoint

HTML documentation ready to be published should be saved here:

```text
GPDO Sharepoint/
  Power BI Documentation/
    Report Name/
```

If this project already has documentation that you need to update, move the old documentation into a folder called Archive in the above Sharepoint directory, in a sub-folder called DD-MMM-YY (e.g. 10-Nov-26). If the Archive folder does not yet exist, create it. Live documentation should go directly in the report's directory.

Finally, create a link in your Power BI report to the Sharepoint documentation folder.
