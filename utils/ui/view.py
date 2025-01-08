'''
   Copyright (C) 2021-2022 Katelynn Cadwallader.

   This file is part of Kuma Kuma Bear, a Discord Bot.

   Kuma Kuma Bear is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 3, or (at your option)
   any later version.

   Kuma Kuma Bear is distributed in the hope that it will be useful, but WITHOUT
   ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
   or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
   License for more details.

   You should have received a copy of the GNU General Public License
   along with Kuma Kuma Bear; see the file COPYING.  If not, write to the Free
   Software Foundation, 51 Franklin Street - Fifth Floor, Boston, MA
   02110-1301, USA. 

'''
from discord.ui import Button,View
from typing import Union, Optional
from discord import ButtonStyle, Interaction, SelectOption
from discord import Emoji, PartialEmoji

class RoleButton(Button):
    """This is for the Reaction Role View"""
    def __init__(self, *, style: ButtonStyle = ButtonStyle.green, label: Optional[str] = None, custom_id: Optional[str] = None, emoji: Union[str, Emoji, PartialEmoji, None]):
        super().__init__(style=style, label=label, custom_id=custom_id, emoji= emoji)

class ReactionRoleView(View):
    def __init__(self, *, timeout: Union[float, None] = 180, custom_id: str, button_label: str, button_emoji: Union[str, Emoji, PartialEmoji, None]) -> None:
        super().__init__(timeout=timeout)
        self.add_item(RoleButton(custom_id= custom_id, label= button_label, emoji= button_emoji))


    

     