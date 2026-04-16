import copy
from PyQt6.QtGui import QUndoCommand
from PyQt6.QtWidgets import QTreeWidgetItem
from PyQt6.QtCore import Qt

class MoveSongCommand(QUndoCommand):
    """Command to move a song within the Setlist."""
    def __init__(self, tree_widget, source_index, dest_index, setlist_tab):
        super().__init__(f"Move Song")
        self.tree_widget = tree_widget
        self.source_index = source_index
        self.dest_index = dest_index
        self.setlist_tab = setlist_tab
        self.is_first_redo = True

    def redo(self):
        if self.is_first_redo:
            self.is_first_redo = False
            return
        
        # Remove from source, insert at dest
        item = self.tree_widget.takeTopLevelItem(self.source_index)
        self.tree_widget.insertTopLevelItem(self.dest_index, item)
        self.setlist_tab.save_setlist_order_silent()

    def undo(self):
        # Remove from dest, insert at source
        item = self.tree_widget.takeTopLevelItem(self.dest_index)
        self.tree_widget.insertTopLevelItem(self.source_index, item)
        self.setlist_tab.save_setlist_order_silent()

class UpdateSetlistCommand(QUndoCommand):
    """Command to replace the entire setlist (for Edit/New Setlist actions)."""
    def __init__(self, setlist_tab, csv_name, old_songs, new_songs):
        super().__init__(f"Update Setlist {csv_name}")
        self.setlist_tab = setlist_tab
        self.csv_name = csv_name
        self.old_songs = old_songs
        self.new_songs = new_songs
    
    def redo(self):
        self.setlist_tab.data_manager.save_setlist(self.csv_name, self.new_songs)
        if self.setlist_tab.current_csv == self.csv_name:
            self.setlist_tab._reload_setlist_silently(self.new_songs)

    def undo(self):
        self.setlist_tab.data_manager.save_setlist(self.csv_name, self.old_songs)
        if self.setlist_tab.current_csv == self.csv_name:
            self.setlist_tab._reload_setlist_silently(self.old_songs)


class TransposeCommand(QUndoCommand):
    """Command to change the transposition (key) of a song."""
    def __init__(self, setlist_tab, song_title, old_steps, new_steps):
        super().__init__(f"Transpose {song_title}")
        self.setlist_tab = setlist_tab
        self.song_title = song_title
        self.old_steps = old_steps
        self.new_steps = new_steps

    def redo(self):
        if self.setlist_tab.current_song == self.song_title:
            self.setlist_tab.transpose_steps = self.new_steps
            self.setlist_tab.lbl_trans.setText(f"Key: {self.new_steps:+d}")
            self.setlist_tab.update_chordpro_view()
        # Save to DB
        self._save_to_db(self.new_steps)

    def undo(self):
        if self.setlist_tab.current_song == self.song_title:
            self.setlist_tab.transpose_steps = self.old_steps
            self.setlist_tab.lbl_trans.setText(f"Key: {self.old_steps:+d}")
            self.setlist_tab.update_chordpro_view()
        # Save to DB
        self._save_to_db(self.old_steps)

    def _save_to_db(self, steps):
        db = self.setlist_tab.data_manager.database
        if self.song_title not in db:
            db[self.song_title] = {}
        db[self.song_title]["Transpose"] = steps
        self.setlist_tab.data_manager.save_database()
