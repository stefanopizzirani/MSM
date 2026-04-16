from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QPen, QBrush
from PyQt6.QtCore import Qt, QRect

def create_chord_pixmap(chord_name: str, fingerings) -> QPixmap:
    """
    Renders a 6-string guitar chord diagram into a QPixmap.
    `fingerings` can be a single string (e.g. '320003') or a list of variations.
    """
    if isinstance(fingerings, str):
        fingerings = [fingerings]
        
    # Cap to max 3 variations for UI sanity
    fingerings = fingerings[:3]
        
    # Create the pixmap surface
    single_width = 160
    height = 200
    width = single_width * len(fingerings)
    
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.white)
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    for v_idx, fingering in enumerate(fingerings):
        offset_x = v_idx * single_width
        
        margin_top = 40
        margin_left = 30 + offset_x
        margin_right = 30 + (width - (offset_x + single_width))
        margin_bottom = 30
        
        diagram_w = single_width - 60
        diagram_h = height - margin_top - margin_bottom
        
        num_strings = 6
        num_frets_to_draw = 5
        string_spacing = diagram_w / (num_strings - 1)
        fret_spacing = diagram_h / num_frets_to_draw
        
        # Analyze fingering
        fingers = []
        frets = []
        
        if isinstance(fingering, list): # Support ['X', 3, 5, 5, 5, 3] format
            for f in fingering:
                if isinstance(f, int):
                    frets.append(f)
                    fingers.append(f)
                else: fingers.append("X")
        else: # Support 'X32010' string format or '8,10,10,8,8,8'
            parts = fingering.split(',') if ',' in fingering else list(fingering)
            for char in parts:
                char = str(char).strip()
                if char.isdigit():
                    frets.append(int(char))
                    fingers.append(int(char))
                else:
                    fingers.append(char.upper()) 
                
        # Determine base fret shifting
        min_fret = 1
        max_fret = max(frets) if frets else 0
        if max_fret > 5:
            min_fret = min([f for f in frets if f > 0])
            
        # Identify barre
        barre_fret = None
        barre_start = 0
        barre_end = 0
        min_played_fret = min([f for f in frets if f > 0], default=0)
        
        if min_played_fret > 0:
            # Ensure the lowest fretted string serves as the barre anchor 
            lowest_str = -1
            for idx in range(num_strings):
                if fingers[idx] != 'X':
                    lowest_str = idx
                    break
                    
            if lowest_str != -1 and fingers[lowest_str] == min_played_fret:
                occurrences = [i for i, f in enumerate(fingers) if f == min_played_fret]
                if len(occurrences) > 1:
                    barre_fret = min_played_fret
                    barre_start = min(occurrences)
                    barre_end = max(occurrences)
                
        # Draw Title
        painter.setPen(QColor("#222222"))
        font = QFont("Arial", 14, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(offset_x, 0, single_width, margin_top - 10, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom, chord_name)
        
        # Draw Nut or Base Fret Number
        if min_fret == 1:
            nut_pen = QPen(QColor("#000000"), 4)
            painter.setPen(nut_pen)
            painter.drawLine(margin_left, margin_top, int(margin_left + diagram_w), margin_top)
        else:
            font = QFont("Arial", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(offset_x + 5, int(margin_top + fret_spacing/2 + 5), f"{min_fret}fr")
            
        # Draw Fret Lines
        fret_pen = QPen(QColor("#666666"), 1)
        for i in range((1 if min_fret == 1 else 0), num_frets_to_draw + 1):
            painter.setPen(fret_pen)
            y = int(margin_top + i * fret_spacing)
            painter.drawLine(margin_left, y, int(margin_left + diagram_w), y)
            
        # Draw Strings
        string_pen = QPen(QColor("#333333"), 1.5)
        for i in range(num_strings):
            painter.setPen(string_pen)
            x = int(margin_left + i * string_spacing)
            painter.drawLine(x, margin_top, x, int(margin_top + diagram_h))
            
            val = fingers[i]
            painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            if val == 'X':
                painter.drawText(x - 5, margin_top - 5, "X")
            elif val == 0:
                painter.drawText(x - 5, margin_top - 5, "O")
                
            # Draw finger dots
            if isinstance(val, int) and val > 0:
                if val == barre_fret and barre_end > barre_start:
                    pass # Drawn later as a line
                else:
                    fret_pos = val - min_fret + 1
                    if 1 <= fret_pos <= num_frets_to_draw:
                        cy = margin_top + (fret_pos - 0.5) * fret_spacing
                        
                        painter.setBrush(QBrush(QColor("#1199FF")))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawEllipse(int(x - 6), int(cy - 6), 12, 12)
                        
        # Draw Barre Line
        if barre_fret and barre_end > barre_start:
            fret_pos = barre_fret - min_fret + 1
            cy = margin_top + (fret_pos - 0.5) * fret_spacing
            sx = margin_left + barre_start * string_spacing
            ex = margin_left + barre_end * string_spacing
            
            barre_pen = QPen(QColor("#1199FF"), 12)
            barre_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(barre_pen)
            painter.drawLine(int(sx), int(cy), int(ex), int(cy))
                
    painter.end()
    return pixmap
