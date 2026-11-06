# SPDX-License-Identifier: Apache-2.0
"""Native MVC checks on a new artificial document, never on a user's model."""
import FreeCAD as App
import FreeCADGui as Gui
import Part
import pft_workbench_tools as tools
import pft_workbench_views as views

name = 'PhoenixToolsUIDemo'
if name in App.listDocuments():
    raise RuntimeError('Test document already exists; refusing to overwrite it.')
doc = App.newDocument(name)
source = doc.addObject('Part::Feature','DemoPart')
source.Shape = Part.makeBox(10,20,5)
source.Label = '7001.2-Demo bracket'
doc.recompute()
fields = [field for field, _ in tools.BOM_FIELDS]
prefs = App.ParamGet('User parameter:BaseApp/Preferences/Document')
preference_before = ('DuplicateLabels' in prefs.GetBools(), prefs.GetBool('DuplicateLabels', False))
created = tools.apply_properties(App,Gui,doc,[source],[tools._snapshot(source)],fields,{},initialize_only=True)
assert created == 5 and all(getattr(source, field) == '' for field in fields)
doc.undo()
assert all(field not in source.PropertiesList for field in fields)
doc.redo()
assert all(field in source.PropertiesList for field in fields)
recommended = tools.recommend_fields_from_label(source.Label)
values = {source.Name:{field:recommended.get(field,'') for field in fields}}
changed = tools.apply_properties(App,Gui,doc,[source],[tools._snapshot(source)],fields,values)
assert source.PartNumber == '7001' and source.PartVersion == '002' and source.PartName == 'Demo bracket'
rows = tools.build_rename_preview(doc,[source],App.listDocuments().values())
assert not rows[0]['error']
renamed = tools.apply_rename(App,Gui,doc,[source],[tools._snapshot(source)],rows)
assert source.Label == '7001.002-Demo bracket'
links = []
for index in range(2):
    link = doc.addObject('App::Link','DemoLink'+str(index+1))
    link.setLink(source)
    link.Label = 'Old instance '+str(index+1)
    links.append(link)
doc.recompute()
old_labels = [link.Label for link in links]
rows = tools.build_link_label_preview(doc,links,App.listDocuments().values())
assert all(not row['error'] for row in rows), [row['error'] for row in rows]
synced = tools.apply_link_labels(App,Gui,doc,links,[tools._snapshot(link) for link in links],rows)
assert synced == 2 and all(link.Label == source.Label for link in links)
doc.undo()
assert [link.Label for link in links] == old_labels
doc.redo()
assert all(link.Label == source.Label for link in links)
assert preference_before == ('DuplicateLabels' in prefs.GetBools(), prefs.GetBool('DuplicateLabels', False))
_, widgets = tools._qt()
old_exec, old_report = views._dialog_exec, views._report
shown, reports = [], []
def cancel(dialog):
    shown.append(dialog.windowTitle())
    return widgets.QDialog.Rejected
views._dialog_exec = cancel
views._report = lambda gui,message,error=False:reports.append((message,error))
before = [tools._snapshot(obj) for obj in doc.Objects]
try:
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(source)
    tools.show_properties_dialog()
    tools.show_rename_dialog()
    Gui.Selection.clearSelection()
    for link in links:
        Gui.Selection.addSelection(link)
    tools.show_link_labels_dialog()
finally:
    views._dialog_exec, views._report = old_exec, old_report
    Gui.Selection.clearSelection()
assert len(shown) == 3 and not reports, reports
assert before == [tools._snapshot(obj) for obj in doc.Objects]
result = {'document':doc.Name,'initialized_fields':created,'updated_fields':changed,'renamed_objects':renamed,'synced_links':synced,'native_undo_redo_passed':True,'duplicate_label_preference_preserved':True,'native_dialogs_built_and_cancelled':shown,'cancel_preserved_data':True,'saved':False,'only_artificial_document_modified':True}
