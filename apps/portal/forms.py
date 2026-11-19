from django import forms
from django.forms import inlineformset_factory

from apps.organizations.models import Organization
from apps.speakers.models import SpeakerLink, SpeakerProfile

MAX_HEADSHOT_MB = 5


class ProfileForm(forms.ModelForm):
    class Meta:
        model = SpeakerProfile
        fields = [
            "first_name", "last_name", "pronouns", "job_title", "affiliation", "bio", "headshot",
            "email", "phone", "city", "region", "country",
        ]  # fmt: skip
        labels = {
            "affiliation": "Organization / affiliation",
            "email": "Contact email",
            "region": "State / region",
            "country": "Country (2-letter code)",
        }
        help_texts = {
            "bio": "A short third-person bio, as it should appear in the schedule.",
            "email": "Shared only with organizations you allow (see Sharing).",
        }
        widgets = {"bio": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name"):
            self.fields[name].required = True
        for name, field in self.fields.items():
            if name != "headshot":
                field.widget.attrs.setdefault("class", "input")

    def clean_headshot(self):
        image = self.cleaned_data.get("headshot")
        if image and hasattr(image, "size") and image.size > MAX_HEADSHOT_MB * 1024 * 1024:
            raise forms.ValidationError(f"Please upload an image under {MAX_HEADSHOT_MB} MB.")
        return image

    def clean_country(self):
        return (self.cleaned_data.get("country") or "").upper()


LinkFormSet = inlineformset_factory(
    SpeakerProfile,
    SpeakerLink,
    fields=["kind", "url", "label"],
    extra=2,
    can_delete=True,
    widgets={
        "kind": forms.Select(attrs={"class": "input"}),
        "url": forms.URLInput(attrs={"class": "input", "placeholder": "https://"}),
        "label": forms.TextInput(attrs={"class": "input", "placeholder": "Label (optional)"}),
    },
)


class GrantForm(forms.Form):
    organization = forms.ModelChoiceField(queryset=Organization.objects.none(), empty_label="Choose an organization")

    def __init__(self, *args, exclude_ids=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = Organization.objects.exclude(pk__in=exclude_ids).order_by("name")
        self.fields["organization"].widget.attrs["class"] = "input"


class SessionEditForm(forms.Form):
    title = forms.CharField(max_length=500, widget=forms.TextInput(attrs={"class": "input"}))
    summary = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 2}), help_text="One or two sentences."
    )
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 8}))


class WithdrawForm(forms.Form):
    reason = forms.CharField(
        required=False,
        label="Anything the organizers should know? (optional)",
        widget=forms.Textarea(attrs={"class": "input", "rows": 3}),
    )


class EventInfoForm(forms.Form):
    """The event's speaker-info questions (pitches.FormQuestion, form=speaker_info) plus recording consent."""

    recording_consent = forms.TypedChoiceField(
        label="May this event record your sessions?",
        choices=[("", "Not answered yet"), ("yes", "Yes"), ("no", "No")],
        coerce=lambda v: {"yes": True, "no": False}.get(v),
        empty_value=None,
        required=False,
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, questions=(), **kwargs):
        from apps.pitches.models import FormQuestion

        super().__init__(*args, **kwargs)
        kind = FormQuestion.Kind
        self.questions = list(questions)
        for q in self.questions:
            common = {"label": q.prompt, "help_text": q.help_text, "required": q.required}
            if q.kind == kind.LONG_TEXT:
                field = forms.CharField(widget=forms.Textarea(attrs={"rows": 4, "class": "input"}), **common)
            elif q.kind == kind.EMAIL:
                field = forms.EmailField(widget=forms.EmailInput(attrs={"class": "input"}), **common)
            elif q.kind == kind.URL:
                field = forms.URLField(widget=forms.URLInput(attrs={"class": "input"}), **common)
            elif q.kind == kind.INTEGER:
                field = forms.IntegerField(widget=forms.NumberInput(attrs={"class": "input"}), **common)
            elif q.kind == kind.BOOLEAN:
                field = forms.BooleanField(**{**common, "required": False})
            elif q.kind == kind.SINGLE_CHOICE:
                field = forms.ChoiceField(choices=[(c, c) for c in q.choices], widget=forms.RadioSelect, **common)
            elif q.kind == kind.MULTI_CHOICE:
                field = forms.MultipleChoiceField(
                    choices=[(c, c) for c in q.choices], widget=forms.CheckboxSelectMultiple, **common
                )
            else:
                field = forms.CharField(widget=forms.TextInput(attrs={"class": "input"}), **common)
            self.fields[f"q_{q.slug}"] = field

    def answers(self) -> dict:
        return {q.slug: self.cleaned_data.get(f"q_{q.slug}") for q in self.questions}
